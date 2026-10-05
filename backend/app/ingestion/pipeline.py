"""Production orchestration for bounded public-source ingestion and refresh."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import Engine, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.ingestion.chunking import ChunkingContext, ChunkingError, SemanticChunker
from app.ingestion.discovery import (
    DiscoveredDocument,
    DiscoveryInputError,
    DiscoveryPolicy,
    DiscoveryReport,
    SourceDiscovery,
    SourceSeed,
    canonicalize_source_url,
)
from app.ingestion.extract import ExtractedDocument, StructuredExtractor
from app.ingestion.indexer import EvidenceIndexer, IndexingError
from app.ingestion.qualification import (
    QualificationEvidence,
    QualificationPolicy,
    SourceQualificationService,
)
from app.ingestion.refresh import SourceRefreshService
from app.ingestion.versions import SourceVersionService
from app.models.enums import IngestionStatus, MediaType, SourceStatus
from app.models.operations import IngestionRun
from app.models.sources import Source, SourceLink, SourceVersion
from app.retrieval.embeddings import EmbeddingError, MiniLMEmbeddingService

Clock = Callable[[], datetime]
DiscoveryRunner = Callable[[DiscoveryPolicy, Sequence[SourceSeed]], DiscoveryReport]
SessionFactory = sessionmaker[Session]
MAX_MANIFEST_SOURCES = 100
MAX_CONFIGURED_TOPICS = 16


class PipelineFailureReason(StrEnum):
    """Bounded reasons returned by the operator surface."""

    MANIFEST_UNAVAILABLE = "manifest_unavailable"
    MANIFEST_INVALID = "manifest_invalid"
    SOURCE_NOT_FOUND = "source_not_found"
    SOURCE_CONFIGURATION_MISSING = "source_configuration_missing"
    DISCOVERY_FAILED = "discovery_failed"
    DOCUMENT_NOT_DISCOVERED = "document_not_discovered"
    INGESTION_FAILED = "ingestion_failed"


class IngestionPipelineError(RuntimeError):
    """A sanitized ingestion failure suitable for conversion to CLI JSON."""

    def __init__(self, reason: PipelineFailureReason) -> None:
        super().__init__(reason.value)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class ManifestImportResult:
    source_ids: tuple[UUID, ...]
    created_source_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class DocumentIngestionResult:
    source_id: UUID
    version_id: UUID
    qualification_id: UUID
    status: SourceStatus
    version_created: bool
    reason_codes: tuple[str, ...]

    @property
    def eligible(self) -> bool:
        return self.status is SourceStatus.ELIGIBLE and not self.reason_codes


@dataclass(frozen=True, slots=True)
class FetchResult:
    requested_source_id: UUID
    documents: tuple[DocumentIngestionResult, ...]
    reason_codes: tuple[str, ...]
    truncated: bool

    @property
    def successful(self) -> bool:
        requested = next(
            (item for item in self.documents if item.source_id == self.requested_source_id),
            None,
        )
        return requested is not None and requested.eligible and not self.reason_codes


@dataclass(frozen=True, slots=True)
class RefreshResult:
    source_ids: tuple[UUID, ...]
    refreshed_source_ids: tuple[UUID, ...]
    failed_source_ids: tuple[UUID, ...]
    expired_source_ids: tuple[UUID, ...]
    reason_codes: tuple[str, ...]


class SourceIngestionPipeline:
    """Own source import, fetch, indexing, qualification, and daily refresh transactions."""

    def __init__(
        self,
        engine: Engine,
        *,
        settings: Settings,
        embedding_service: MiniLMEmbeddingService | None = None,
        discovery_runner: DiscoveryRunner | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._engine = engine
        self._settings = settings
        self._embedding_service = embedding_service or MiniLMEmbeddingService.from_settings(
            settings
        )
        self._discovery_runner = discovery_runner or _discover
        self._clock = clock or _utc_now
        self._session_factory: SessionFactory = sessionmaker(
            bind=engine,
            expire_on_commit=False,
        )

    def import_manifest(self, path: Path) -> ManifestImportResult:
        """Validate and idempotently persist bounded source candidates."""

        seeds = load_source_manifest(path, policy=DiscoveryPolicy.from_settings(self._settings))
        source_ids: list[UUID] = []
        created_ids: list[UUID] = []
        try:
            with self._session_factory.begin() as session:
                for seed in seeds:
                    canonical_url = canonicalize_source_url(
                        seed.url,
                        allowed_hosts=self._settings.source_allowed_hosts,
                        allowed_schemes=self._settings.source_allowed_schemes,
                    )
                    source = session.scalar(
                        select(Source)
                        .where(Source.canonical_url == canonical_url)
                        .with_for_update()
                    )
                    if source is None:
                        source = Source(
                            canonical_url=canonical_url,
                            title=seed.title,
                            media_type=_media_type_from_url(canonical_url),
                            status=SourceStatus.CANDIDATE,
                            configured_topics=list(seed.topics),
                            allowed_child_hosts=list(seed.allowed_child_hosts),
                        )
                        session.add(source)
                        session.flush()
                        created_ids.append(source.id)
                    else:
                        source.title = seed.title
                        source.configured_topics = list(seed.topics)
                        source.allowed_child_hosts = list(seed.allowed_child_hosts)
                    source_ids.append(source.id)
        except SQLAlchemyError:
            raise IngestionPipelineError(PipelineFailureReason.INGESTION_FAILED) from None
        return ManifestImportResult(tuple(source_ids), tuple(created_ids))

    def fetch(self, source_id: UUID) -> FetchResult:
        """Fetch one configured root and independently ingest each bounded linked document."""

        source, seed = self._configured_seed(source_id)
        policy = DiscoveryPolicy.from_settings(self._settings)
        try:
            report = self._discovery_runner(policy, (seed,))
        except (DiscoveryInputError, OSError):
            self._record_fetch_failure(source_id, PipelineFailureReason.DISCOVERY_FAILED.value)
            return FetchResult(
                requested_source_id=source_id,
                documents=(),
                reason_codes=(PipelineFailureReason.DISCOVERY_FAILED.value,),
                truncated=False,
            )

        if not report.documents:
            reason_codes = tuple(dict.fromkeys(issue.reason.value for issue in report.issues)) or (
                PipelineFailureReason.DOCUMENT_NOT_DISCOVERED.value,
            )
            self._record_fetch_failure(source_id, reason_codes[0])
            return FetchResult(source_id, (), reason_codes, report.truncated)

        observed_at = _aware_utc(self._clock())
        outcomes: list[DocumentIngestionResult] = []
        for document in report.documents:
            outcomes.append(
                self._ingest_document(
                    document,
                    root=source,
                    seed=seed,
                    observed_at=observed_at,
                )
            )
        self._record_links(report.documents)
        requested_present = any(item.source_id == source_id for item in outcomes)
        issue_reasons = tuple(
            dict.fromkeys(
                (
                    *(issue.reason.value for issue in report.issues),
                    *(
                        ()
                        if requested_present
                        else (PipelineFailureReason.DOCUMENT_NOT_DISCOVERED.value,)
                    ),
                )
            )
        )
        outcome_reasons = tuple(
            dict.fromkeys(
                reason
                for outcome in outcomes
                if not outcome.eligible
                for reason in outcome.reason_codes
            )
        )
        return FetchResult(
            requested_source_id=source_id,
            documents=tuple(outcomes),
            reason_codes=tuple(dict.fromkeys((*issue_reasons, *outcome_reasons))),
            truncated=report.truncated,
        )

    def refresh_due(self) -> RefreshResult:
        """Expire stale authority, then run the same production pipeline for every due source."""

        try:
            with self._session_factory.begin() as session:
                lifecycle = SourceRefreshService(session, clock=self._clock)
                expired = lifecycle.expire_due()
                due_ids = tuple(source.id for source in lifecycle.due_sources())
        except SQLAlchemyError:
            raise IngestionPipelineError(PipelineFailureReason.INGESTION_FAILED) from None

        refreshed: list[UUID] = []
        failed: list[UUID] = []
        reasons: list[str] = []
        for source_id in due_ids:
            try:
                result = self.fetch(source_id)
            except IngestionPipelineError as error:
                failed.append(source_id)
                reasons.append(error.reason.value)
                continue
            if result.successful:
                refreshed.append(source_id)
            else:
                failed.append(source_id)
                reasons.extend(result.reason_codes)
        return RefreshResult(
            source_ids=due_ids,
            refreshed_source_ids=tuple(refreshed),
            failed_source_ids=tuple(failed),
            expired_source_ids=tuple(item.source_id for item in expired),
            reason_codes=tuple(dict.fromkeys(reasons)),
        )

    def _configured_seed(self, source_id: UUID) -> tuple[Source, SourceSeed]:
        try:
            with self._session_factory() as session:
                source = session.get(Source, source_id)
                if source is None:
                    raise IngestionPipelineError(PipelineFailureReason.SOURCE_NOT_FOUND)
                topics = tuple(source.configured_topics)
                child_hosts = tuple(source.allowed_child_hosts)
                if not topics or not child_hosts:
                    raise IngestionPipelineError(PipelineFailureReason.SOURCE_CONFIGURATION_MISSING)
                detached = Source(
                    id=source.id,
                    canonical_url=source.canonical_url,
                    title=source.title,
                    media_type=source.media_type,
                    status=source.status,
                    configured_topics=list(topics),
                    allowed_child_hosts=list(child_hosts),
                )
                return detached, SourceSeed(
                    url=source.canonical_url,
                    title=source.title,
                    topics=topics,
                    allowed_child_hosts=child_hosts,
                )
        except IngestionPipelineError:
            raise
        except SQLAlchemyError:
            raise IngestionPipelineError(PipelineFailureReason.INGESTION_FAILED) from None

    def _ingest_document(
        self,
        document: DiscoveredDocument,
        *,
        root: Source,
        seed: SourceSeed,
        observed_at: datetime,
    ) -> DocumentIngestionResult:
        try:
            with self._session_factory.begin() as session:
                source = _upsert_discovered_source(
                    session,
                    document=document,
                    root=root,
                    seed=seed,
                )
                run = IngestionRun(
                    source_id=source.id,
                    started_at=observed_at,
                    status=IngestionStatus.RUNNING,
                )
                session.add(run)
                session.flush()
                extraction = StructuredExtractor().extract(document)
                version_write = SourceVersionService(
                    session,
                    clock=lambda: observed_at,
                ).record(
                    source=source,
                    document=document,
                    extraction=extraction,
                    ingestion_run=run,
                )
                processing_reason: str | None = None
                if extraction.complete:
                    try:
                        context = _chunking_context(source, extraction)
                        chunks = SemanticChunker(self._embedding_service.count_wordpieces).chunk(
                            version=version_write.version,
                            document=extraction,
                            context=context,
                        )
                        EvidenceIndexer(
                            session,
                            embedding_service=self._embedding_service,
                        ).index(version=version_write.version, chunks=chunks)
                    except (ChunkingError, IndexingError, EmbeddingError) as error:
                        reason = getattr(error, "reason", None)
                        processing_reason = (
                            reason.value if isinstance(reason, StrEnum) else "ingestion_failed"
                        )
                        run.status = IngestionStatus.FAILED
                        run.completed_at = observed_at
                        run.bounded_error_code = processing_reason

                qualification = SourceQualificationService(
                    session,
                    policy=QualificationPolicy.from_settings(self._settings),
                    clock=lambda: observed_at,
                ).qualify(
                    source=source,
                    version=version_write.version,
                    evidence=QualificationEvidence(
                        document=document,
                        observed_at=observed_at,
                    ),
                )
                reasons = tuple(
                    dict.fromkeys(
                        (
                            *(reason.value for reason in version_write.reason_codes),
                            *(reason.value for reason in qualification.reason_codes),
                        )
                    )
                )
                if processing_reason is not None:
                    reasons = tuple(dict.fromkeys((processing_reason, *reasons)))
                return DocumentIngestionResult(
                    source_id=source.id,
                    version_id=version_write.version.id,
                    qualification_id=qualification.qualification.id,
                    status=source.status,
                    version_created=version_write.created,
                    reason_codes=reasons,
                )
        except SQLAlchemyError:
            raise IngestionPipelineError(PipelineFailureReason.INGESTION_FAILED) from None

    def _record_links(self, documents: Sequence[DiscoveredDocument]) -> None:
        linked = tuple(document for document in documents if document.parent_url is not None)
        if not linked:
            return
        try:
            with self._session_factory.begin() as session:
                urls = {document.canonical_url for document in linked} | {
                    cast(str, document.parent_url) for document in linked
                }
                sources = {
                    source.canonical_url: source
                    for source in session.scalars(
                        select(Source).where(Source.canonical_url.in_(urls))
                    ).all()
                }
                for document in linked:
                    parent = sources.get(cast(str, document.parent_url))
                    child = sources.get(document.canonical_url)
                    if parent is None or child is None:
                        continue
                    parent_version = _latest_version(session, parent.id)
                    if parent_version is None:
                        continue
                    key = {
                        "from_version_id": parent_version.id,
                        "target_source_id": child.id,
                        "relation": "discovered",
                    }
                    if session.get(SourceLink, key) is None:
                        session.add(SourceLink(**key))
        except SQLAlchemyError:
            raise IngestionPipelineError(PipelineFailureReason.INGESTION_FAILED) from None

    def _record_fetch_failure(self, source_id: UUID, reason_code: str) -> None:
        try:
            with self._session_factory.begin() as session:
                SourceRefreshService(session, clock=self._clock).record_refresh_failure(
                    source_id=source_id,
                    reason_code=reason_code,
                )
        except SQLAlchemyError:
            raise IngestionPipelineError(PipelineFailureReason.INGESTION_FAILED) from None


def load_source_manifest(path: Path, *, policy: DiscoveryPolicy) -> tuple[SourceSeed, ...]:
    """Parse the exact source-operations manifest shape without accepting extra fields."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise IngestionPipelineError(PipelineFailureReason.MANIFEST_UNAVAILABLE) from None
    if not isinstance(payload, dict) or set(payload) != {"sources"}:
        raise IngestionPipelineError(PipelineFailureReason.MANIFEST_INVALID)
    raw_sources = payload["sources"]
    if (
        not isinstance(raw_sources, list)
        or not raw_sources
        or len(raw_sources) > MAX_MANIFEST_SOURCES
    ):
        raise IngestionPipelineError(PipelineFailureReason.MANIFEST_INVALID)

    seeds: list[SourceSeed] = []
    canonical_urls: set[str] = set()
    required = {"url", "title", "topics", "allowed_child_hosts"}
    try:
        for raw in raw_sources:
            if not isinstance(raw, dict) or set(raw) != required:
                raise IngestionPipelineError(PipelineFailureReason.MANIFEST_INVALID)
            topics = raw["topics"]
            hosts = raw["allowed_child_hosts"]
            if (
                not isinstance(raw["url"], str)
                or not isinstance(raw["title"], str)
                or not isinstance(topics, list)
                or not all(isinstance(item, str) for item in topics)
                or len(topics) > MAX_CONFIGURED_TOPICS
                or not isinstance(hosts, list)
                or not all(isinstance(item, str) for item in hosts)
                or len(hosts) > 3
            ):
                raise IngestionPipelineError(PipelineFailureReason.MANIFEST_INVALID)
            seed = SourceSeed(
                url=cast(str, raw["url"]),
                title=cast(str, raw["title"]),
                topics=tuple(topics),
                allowed_child_hosts=tuple(hosts),
            )
            canonical = canonicalize_source_url(
                seed.url,
                allowed_hosts=policy.allowed_hosts,
                allowed_schemes=policy.allowed_schemes,
            )
            if canonical in canonical_urls:
                continue
            canonical_urls.add(canonical)
            seeds.append(seed)
    except (DiscoveryInputError, KeyError, TypeError):
        raise IngestionPipelineError(PipelineFailureReason.MANIFEST_INVALID) from None
    return tuple(seeds)


def _upsert_discovered_source(
    session: Session,
    *,
    document: DiscoveredDocument,
    root: Source,
    seed: SourceSeed,
) -> Source:
    source = session.scalar(
        select(Source).where(Source.canonical_url == document.canonical_url).with_for_update()
    )
    if source is None:
        source = Source(
            canonical_url=document.canonical_url,
            title=document.title,
            media_type=document.media_type,
            status=SourceStatus.CANDIDATE,
            configured_topics=list(seed.topics),
            allowed_child_hosts=list(seed.allowed_child_hosts),
        )
        session.add(source)
        session.flush()
        return source
    if source.media_type is not document.media_type and not source.versions:
        source.media_type = document.media_type
    source.title = document.title
    if not source.configured_topics:
        source.configured_topics = list(seed.topics)
    if not source.allowed_child_hosts:
        source.allowed_child_hosts = list(seed.allowed_child_hosts)
    return source


def _chunking_context(source: Source, extraction: ExtractedDocument) -> ChunkingContext:
    campus = extraction.campus_labels[0] if len(extraction.campus_labels) == 1 else None
    catalog = extraction.catalog_context[0] if len(extraction.catalog_context) == 1 else None
    return ChunkingContext(
        topic_key=source.configured_topics[0],
        campus=campus,
        catalog_year=catalog,
        scope={
            "configured_topics": list(source.configured_topics),
            "source_classification": "manifest_and_extracted_metadata",
        },
    )


def _latest_version(session: Session, source_id: UUID) -> SourceVersion | None:
    return session.scalar(
        select(SourceVersion)
        .where(SourceVersion.source_id == source_id)
        .order_by(SourceVersion.fetched_at.desc(), SourceVersion.id.desc())
        .limit(1)
    )


def _discover(policy: DiscoveryPolicy, seeds: Sequence[SourceSeed]) -> DiscoveryReport:
    with SourceDiscovery(policy) as discovery:
        return discovery.discover(seeds)


def _media_type_from_url(url: str) -> MediaType:
    return MediaType.PDF if urlsplit(url).path.casefold().endswith(".pdf") else MediaType.HTML


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("ingestion clock must be timezone-aware")
    return value.astimezone(UTC)


def _utc_now() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "DocumentIngestionResult",
    "FetchResult",
    "IngestionPipelineError",
    "ManifestImportResult",
    "PipelineFailureReason",
    "RefreshResult",
    "SourceIngestionPipeline",
    "load_source_manifest",
]
