from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import AsyncSessionLocal
from app.models import (
    CorpusBuild,
    CorpusBuildStatus,
    IngestionStatus,
    SourceChunk,
    SourceDocument,
    SourceStatus,
    SourceType,
)
from app.services.corpus_service import CorpusService
from app.services.embedding_service import OllamaEmbeddingProvider, get_embedding_provider
from ingestion.chunk_content import Chunk, chunk_content
from ingestion.embed_and_index import build_corpus_version, embed_and_index
from ingestion.extract_content import ExtractedContent, extract_document
from ingestion.fetch_sources import FetchedSource, SourceFetcher

DEFAULT_MANIFEST_PATH = Path(__file__).with_name("sources.yaml")


@dataclass(frozen=True, slots=True)
class BuildValidationResult:
    ok: bool
    reason: str | None = None


def validate_build_requirements(build: CorpusBuild) -> None:
    """Ensure a corpus build is complete before it can be promoted.

    The build must have at least one source and one chunk, a valid embedding model
    configuration, and a status that indicates it is ready for validation.
    """
    if build.source_count <= 0:
        raise ValueError("corpus build must include at least one source before validation")
    if build.chunk_count <= 0:
        raise ValueError("corpus build must include at least one chunk before validation")
    if build.embedding_model.strip() == "":
        raise ValueError("embedding model name must not be empty")
    if build.embedding_dimensions <= 0:
        raise ValueError("embedding dimensions must be positive")
    if build.status not in {CorpusBuildStatus.VALIDATED, CorpusBuildStatus.BUILDING}:
        raise ValueError("corpus build must be in BUILDING or VALIDATED state before promotion")


def mark_build_failed(build: CorpusBuild, *, reason: str) -> CorpusBuild:
    """Mark a build as failed and ensure it cannot be promoted."""
    build.status = CorpusBuildStatus.FAILED
    build.validation_summary = reason
    build.completed_at = datetime.now(UTC)
    return build


def promote_if_valid(build: CorpusBuild) -> CorpusBuild:
    """Promote a build only if all required checks pass."""
    validate_build_requirements(build)
    build.status = CorpusBuildStatus.VALIDATED
    build.completed_at = datetime.now(UTC)
    return build


def rollback_build(build: CorpusBuild) -> CorpusBuild:
    """Reset a failed or incomplete build to a safe terminal state."""
    if build.status in {CorpusBuildStatus.BUILDING, CorpusBuildStatus.VALIDATED}:
        build.status = CorpusBuildStatus.FAILED
    build.completed_at = datetime.now(UTC)
    return build


def load_manifest(manifest_path: Path) -> dict[str, Any]:
    with manifest_path.open(encoding="utf-8") as manifest_file:
        manifest = yaml.safe_load(manifest_file)
    if not isinstance(manifest, dict):
        raise TypeError("source manifest must contain a YAML mapping")
    if not isinstance(manifest.get("defaults"), dict):
        raise TypeError("source manifest must define defaults")
    if not isinstance(manifest.get("sources"), list):
        raise TypeError("source manifest must define a sources list")
    return manifest


def _parse_review_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip()[:10])
        except ValueError:
            return None
    return None


def select_approved_sources(
    sources: list[dict[str, Any]],
    review_defaults: dict[str, Any],
    *,
    today: date | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Select explicitly approved, recently reviewed sources from a manifest."""
    current_date = today or datetime.now(UTC).date()
    review_cadence = review_defaults.get("default_cadence_days", 30)
    if not isinstance(review_cadence, int) or review_cadence <= 0:
        raise ValueError("default review cadence must be a positive number of days")

    approved: list[dict[str, Any]] = []
    skipped: list[str] = []
    seen_ids: set[str] = set()
    for source in sources:
        if not isinstance(source, dict):
            raise TypeError("each source manifest entry must be a mapping")
        source_id = source.get("id")
        if not isinstance(source_id, str) or not source_id.strip():
            raise ValueError("every source must have a non-empty id")
        source_id = source_id.strip()
        if source_id in seen_ids:
            raise ValueError(f"duplicate source id in manifest: {source_id}")
        seen_ids.add(source_id)

        review_status = str(source.get("review_status", "")).strip().lower()
        if review_status not in {"active", "approved"}:
            skipped.append(source_id)
            continue

        reviewer = source.get("reviewer")
        reviewed_at = _parse_review_date(source.get("last_reviewed"))
        cadence = source.get("review_cadence_days", review_cadence)
        if not isinstance(cadence, int) or cadence <= 0:
            raise ValueError(f"source {source_id!r} has an invalid review cadence")
        if not isinstance(reviewer, str) or not reviewer.strip() or reviewed_at is None:
            raise ValueError(
                f"approved source {source_id!r} requires reviewer and last_reviewed metadata"
            )
        age_days = (current_date - reviewed_at).days
        if age_days < 0 or age_days > cadence:
            raise ValueError(
                f"approved source {source_id!r} must be reviewed within its {cadence}-day cadence"
            )
        for field in ("title", "url", "source_type"):
            if not isinstance(source.get(field), str) or not source[field].strip():
                raise ValueError(f"source {source_id!r} requires a non-empty {field}")
        approved.append(source)

    if not approved:
        raise ValueError(
            "no currently approved sources; set review_status to active, provide "
            "reviewer and a recent last_reviewed date in sources.yaml"
        )
    return approved, skipped


@dataclass(frozen=True, slots=True)
class PreparedSource:
    manifest: dict[str, Any]
    fetched: FetchedSource
    extracted: ExtractedContent
    chunks: list[Chunk]


async def _prepare_sources(
    sources: list[dict[str, Any]], fetcher: SourceFetcher
) -> list[PreparedSource]:
    prepared: list[PreparedSource] = []
    seen_urls: set[str] = set()
    for source in sources:
        fetched = await fetcher.fetch(source["url"])
        if fetched.canonical_url in seen_urls:
            raise ValueError(f"multiple manifest entries resolve to {fetched.canonical_url}")
        seen_urls.add(fetched.canonical_url)
        if fetched.content_type not in {"text/html", "application/pdf", "text/plain"}:
            raise ValueError(
                f"source {source['id']!r} has unsupported extraction type "
                f"{fetched.content_type!r}; only HTML, PDF, and plain text are supported"
            )

        extracted = extract_document(
            fetched.content,
            fetched.canonical_url,
            content_type=fetched.content_type,
        )
        if extracted.issues:
            issues = "; ".join(issue.message for issue in extracted.issues)
            raise ValueError(f"source {source['id']!r} could not be extracted: {issues}")
        chunks = chunk_content(extracted)
        if not chunks:
            raise ValueError(f"source {source['id']!r} produced no non-empty chunks")
        prepared.append(PreparedSource(source, fetched, extracted, chunks))
    return prepared


def _updated_date(extracted: ExtractedContent, source: dict[str, Any]) -> date | None:
    for _, value in reversed(extracted.update_metadata):
        try:
            return datetime.fromisoformat(value).date()
        except ValueError:
            try:
                return parsedate_to_datetime(value).date()
            except (TypeError, ValueError):
                continue
    return _parse_review_date(source.get("last_reviewed"))


async def _persist_and_embed(
    session: AsyncSession,
    prepared_sources: list[PreparedSource],
    *,
    corpus_version: str,
    provider: OllamaEmbeddingProvider,
) -> list[SourceChunk]:
    chunks_to_embed: list[SourceChunk] = []
    for prepared in prepared_sources:
        source = prepared.manifest
        source_type = (
            SourceType.PDF
            if prepared.fetched.content_type == "application/pdf"
            else SourceType(source["source_type"])
        )
        result = await session.execute(
            select(SourceDocument)
            .where(SourceDocument.url == prepared.fetched.canonical_url)
            .with_for_update()
        )
        document = result.scalar_one_or_none()
        if document is None:
            document = SourceDocument(
                id=uuid4(),
                title=source["title"],
                url=prepared.fetched.canonical_url,
                source_type=source_type,
                campus_scope=source.get("campus_scope"),
                academic_term=source.get("academic_term"),
                last_updated=_updated_date(prepared.extracted, source),
                status=SourceStatus.ACTIVE,
                reviewed_by=source["reviewer"].strip(),
                content_hash=prepared.fetched.content_hash,
                ingestion_status=IngestionStatus.EXTRACTED,
                corpus_version=corpus_version,
            )
        else:
            document.title = source["title"]
            document.source_type = source_type
            document.campus_scope = source.get("campus_scope")
            document.academic_term = source.get("academic_term")
            document.last_updated = _updated_date(prepared.extracted, source)
            document.status = SourceStatus.ACTIVE
            document.reviewed_by = source["reviewer"].strip()
            document.content_hash = prepared.fetched.content_hash
            document.ingestion_status = IngestionStatus.EXTRACTED
            document.corpus_version = corpus_version
        session.add(document)
        await session.flush()

        for index, chunk in enumerate(prepared.chunks, start=1):
            source_chunk = SourceChunk(
                id=uuid4(),
                document_id=document.id,
                chunk_index=index,
                heading=chunk.heading,
                content=chunk.content,
                page_ref=chunk.page_ref,
                corpus_version=corpus_version,
            )
            session.add(source_chunk)
            chunks_to_embed.append(source_chunk)

    await session.flush()
    await embed_and_index(
        session,
        chunks=chunks_to_embed,
        corpus_version=corpus_version,
        provider=provider,
    )
    for prepared in prepared_sources:
        result = await session.execute(
            select(SourceDocument).where(SourceDocument.url == prepared.fetched.canonical_url)
        )
        document = result.scalar_one()
        document.ingestion_status = IngestionStatus.VALIDATED
    await session.flush()
    return chunks_to_embed


async def run_corpus_build(
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
) -> tuple[CorpusBuild, list[str]]:
    manifest = load_manifest(manifest_path)
    defaults = manifest["defaults"]
    review_defaults = defaults.get("review", {})
    sources, skipped = select_approved_sources(
        manifest["sources"],
        review_defaults if isinstance(review_defaults, dict) else {},
    )
    allowed_hosts = defaults.get("allowed_hosts")
    if not isinstance(allowed_hosts, list) or not allowed_hosts:
        raise ValueError("source manifest must define a non-empty allowed_hosts list")
    follow_link_defaults = defaults.get("follow_links", {})
    if not isinstance(follow_link_defaults, dict):
        raise TypeError("follow_links defaults must be a mapping")
    allowed_content_types = follow_link_defaults.get("allowed_content_types", [])
    if not isinstance(allowed_content_types, list):
        raise TypeError("allowed_content_types must be a list")

    provider = get_embedding_provider()
    version = build_corpus_version(f"corpus-{uuid4().hex[:8]}")
    build: CorpusBuild
    failure: Exception | None = None
    settings = get_settings()

    async with httpx.AsyncClient() as client:
        fetcher = SourceFetcher(
            client,
            allowed_hosts=allowed_hosts,
            allowed_content_types=allowed_content_types,
        )
        try:
            async with AsyncSessionLocal() as session, session.begin():
                build = CorpusBuild(
                    version=version,
                    embedding_model=provider.config.model_name,
                    embedding_dimensions=provider.config.dimensions,
                    source_count=0,
                    chunk_count=0,
                    status=CorpusBuildStatus.BUILDING,
                    started_at=datetime.now(UTC),
                )
                session.add(build)
                await session.flush()
                try:
                    async with session.begin_nested():
                        prepared = await _prepare_sources(sources, fetcher)
                        chunks = await _persist_and_embed(
                            session,
                            prepared,
                            corpus_version=version,
                            provider=provider,
                        )
                        build.source_count = len(prepared)
                        build.chunk_count = len(chunks)
                        if provider.config.dimensions != settings.embedding_dimensions:
                            raise ValueError(
                                "embedding provider dimensions do not match configured dimensions"
                            )
                        if any(
                            chunk.embedding is None
                            or len(chunk.embedding) != provider.config.dimensions
                            for chunk in chunks
                        ):
                            raise ValueError("candidate corpus contains invalid embeddings")
                        await session.flush()
                        promote_if_valid(build)
                        await session.flush()
                        await CorpusService(session).promote(version)
                except Exception as error:  # noqa: BLE001
                    failure = error
                    mark_build_failed(
                        build,
                        reason=f"{type(error).__name__}: {error}",
                    )
                    await session.flush()
        finally:
            await provider.aclose()

    if failure is not None:
        raise RuntimeError(f"corpus build {version!r} failed: {failure}") from failure
    return build, skipped


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch approved sources, build embeddings, and promote a complete corpus."
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST_PATH,
        help="source manifest YAML (default: ingestion/sources.yaml)",
    )
    args = parser.parse_args(argv)
    try:
        build, skipped = asyncio.run(run_corpus_build(args.manifest))
    except Exception as error:  # noqa: BLE001
        print(f"Corpus build failed: {error}", file=sys.stderr)
        return 1

    print(
        f"Promoted corpus {build.version}: "
        f"{build.source_count} sources, {build.chunk_count} chunks, "
        f"{build.embedding_model} ({build.embedding_dimensions} dimensions)."
    )
    if skipped:
        print(f"Skipped sources pending approval: {', '.join(skipped)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
