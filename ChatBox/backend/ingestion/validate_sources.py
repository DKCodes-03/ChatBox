from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from ingestion.build_corpus import DEFAULT_MANIFEST_PATH, load_manifest
from ingestion.chunk_content import chunk_content
from ingestion.extract_content import extract_document
from ingestion.fetch_sources import SourceFetcher

SUPPORTED_EXTRACTION_TYPES = frozenset({"text/html", "text/plain", "application/pdf"})


@dataclass(frozen=True, slots=True)
class SourceValidationResult:
    source_id: str
    title: str
    url: str
    ok: bool
    content_type: str | None = None
    canonical_url: str | None = None
    chunk_count: int = 0
    error: str | None = None


async def validate_sources(
    sources: list[dict[str, Any]], fetcher: SourceFetcher
) -> list[SourceValidationResult]:
    """Fetch each manifest URL and confirm it extracts into indexable chunks."""
    results: list[SourceValidationResult] = []
    for index, source in enumerate(sources, start=1):
        source_id = source.get("id")
        if not isinstance(source_id, str) or not source_id.strip():
            source_id = f"source-{index}"
        title = source.get("title")
        if not isinstance(title, str) or not title.strip():
            title = source_id
        url = source.get("url")
        if not isinstance(url, str) or not url.strip():
            results.append(
                SourceValidationResult(
                    source_id=source_id,
                    title=title,
                    url="",
                    ok=False,
                    error="manifest source has no URL",
                )
            )
            continue

        try:
            fetched = await fetcher.fetch(url)
            if fetched.content_type not in SUPPORTED_EXTRACTION_TYPES:
                raise ValueError(
                    f"fetched as {fetched.content_type!r}, but the extractor supports "
                    "HTML, plain text, and PDF only"
                )

            extracted = extract_document(
                fetched.content,
                fetched.canonical_url,
                content_type=fetched.content_type,
            )
            if extracted.issues:
                details = "; ".join(f"{issue.code}: {issue.message}" for issue in extracted.issues)
                raise ValueError(f"content extraction failed: {details}")

            chunks = chunk_content(extracted)
            if not chunks:
                raise ValueError("content parsed, but produced no indexable chunks")

            results.append(
                SourceValidationResult(
                    source_id=source_id,
                    title=title,
                    url=url,
                    ok=True,
                    content_type=fetched.content_type,
                    canonical_url=fetched.canonical_url,
                    chunk_count=len(chunks),
                )
            )
        except Exception as error:  # noqa: BLE001
            results.append(
                SourceValidationResult(
                    source_id=source_id,
                    title=title,
                    url=url,
                    ok=False,
                    error=f"{type(error).__name__}: {error}",
                )
            )
    return results


async def run_validation(
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
) -> list[SourceValidationResult]:
    manifest = load_manifest(manifest_path)
    defaults = manifest["defaults"]
    allowed_hosts = defaults.get("allowed_hosts")
    if not isinstance(allowed_hosts, list) or not allowed_hosts:
        raise ValueError("source manifest must define a non-empty allowed_hosts list")
    follow_links = defaults.get("follow_links", {})
    if not isinstance(follow_links, dict):
        raise TypeError("follow_links defaults must be a mapping")
    allowed_content_types = follow_links.get("allowed_content_types", [])
    if not isinstance(allowed_content_types, list):
        raise TypeError("allowed_content_types must be a list")

    async with httpx.AsyncClient() as client:
        fetcher = SourceFetcher(
            client,
            allowed_hosts=allowed_hosts,
            allowed_content_types=allowed_content_types,
        )
        return await validate_sources(manifest["sources"], fetcher)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check manifest URLs for fetch, extraction, and chunking readiness."
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST_PATH,
        help="source manifest YAML (default: ingestion/sources.yaml)",
    )
    args = parser.parse_args(argv)

    try:
        results = asyncio.run(run_validation(args.manifest))
    except Exception as error:  # noqa: BLE001
        print(f"Source validation failed: {error}", file=sys.stderr)
        return 1

    for result in results:
        if result.ok:
            print(
                f"PASS {result.source_id}: {result.content_type}, "
                f"{result.chunk_count} chunks, {result.canonical_url}"
            )
        else:
            print(f"FAIL {result.source_id}: {result.url} ({result.error})")

    passed = sum(result.ok for result in results)
    failed = len(results) - passed
    print(f"Results: {passed} passed, {failed} failed, {len(results)} total")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
