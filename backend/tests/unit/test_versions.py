"""Immutable version, hashing, state, and quarantine checks."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

import pytest
from app.ingestion.discovery import DiscoveredDocument
from app.ingestion.extract import ExtractionFailureReason, StructuredExtractor
from app.ingestion.versions import (
    ImmutableVersionError,
    SourceVersionService,
    VersionConflictError,
    VersionInputError,
    content_sha256,
    normalized_source_content,
)
from app.models.enums import (
    ExtractionStatus,
    IngestionStatus,
    MediaType,
    SourceEventType,
    SourceStatus,
)
from app.models.operations import IngestionRun, SourceEvent
from app.models.sources import Source, SourceVersion
from pytest import MonkeyPatch
from sqlalchemy.orm import Session, make_transient_to_detached

OBSERVED_AT = datetime(2030, 1, 15, 12, 0, tzinfo=UTC)
SOURCE_URL = "https://www.pnw.edu/policy/"


def _document(
    content: bytes,
    media_type: MediaType = MediaType.HTML,
    *,
    url: str = SOURCE_URL,
) -> DiscoveredDocument:
    return DiscoveredDocument(
        requested_url=url,
        canonical_url=url,
        title="Policy title",
        topics=("registration",),
        media_type=media_type,
        content=content,
        depth=0,
        parent_url=None,
        redirect_chain=(),
    )


def _source(
    media_type: MediaType = MediaType.HTML,
    *,
    status: SourceStatus = SourceStatus.CANDIDATE,
) -> Source:
    return Source(
        id=uuid4(),
        canonical_url=SOURCE_URL,
        title="Policy title",
        media_type=media_type,
        status=status,
    )


def _run(source: Source) -> IngestionRun:
    return IngestionRun(
        id=uuid4(),
        source_id=source.id,
        started_at=OBSERVED_AT,
        status=IngestionStatus.RUNNING,
    )


def _fake_session(
    monkeypatch: MonkeyPatch,
    *,
    existing: SourceVersion | None = None,
) -> tuple[Session, list[object]]:
    session = Session()
    added: list[object] = []

    def add(instance: object, _warn: bool = True) -> None:
        _ = _warn
        added.append(instance)

    def flush(_objects: object = None) -> None:
        for instance in added:
            if isinstance(instance, (SourceVersion, SourceEvent)) and instance.id is None:
                instance.id = uuid4()

    monkeypatch.setattr(session, "scalar", lambda _statement: existing)
    monkeypatch.setattr(session, "add", add)
    monkeypatch.setattr(session, "flush", flush)
    return session, added


def test_hash_uses_exact_fetched_bytes_while_html_storage_normalizes_encoding() -> None:
    content = b"\xef\xbb\xbf<main>\r\n<h1>Policy</h1>\r\n<p>Condition.</p>\r\n</main>"
    document = _document(content)
    extraction = StructuredExtractor().extract(document)

    assert content_sha256(document) == sha256(content).hexdigest()
    assert normalized_source_content(document, extraction).startswith("<main>\n")
    assert "\r" not in normalized_source_content(document, extraction)


def test_complete_extraction_creates_an_immutable_candidate_version(
    monkeypatch: MonkeyPatch,
) -> None:
    content = b"<main><h1>Policy</h1><p>A supported condition.</p></main>"
    document = _document(content)
    extraction = StructuredExtractor().extract(document)
    source = _source()
    run = _run(source)
    session, added = _fake_session(monkeypatch)

    result = SourceVersionService(session, clock=lambda: OBSERVED_AT).record(
        source=source,
        document=document,
        extraction=extraction,
        ingestion_run=run,
    )

    assert result.created is True
    assert result.extraction_status is ExtractionStatus.COMPLETE
    assert result.quarantined is False
    assert result.reason_codes == ()
    assert result.version.content_sha256 == sha256(content).hexdigest()
    assert result.version.content == content.decode()
    assert result.version.fetched_at == OBSERVED_AT
    assert result.version.parser_version == extraction.parser_version
    assert source.status is SourceStatus.CANDIDATE
    assert run.status is IngestionStatus.SUCCEEDED
    assert run.completed_at == OBSERVED_AT
    assert run.version_id == result.version.id
    assert run.bounded_error_code is None
    assert len([item for item in added if isinstance(item, SourceVersion)]) == 1
    assert not any(isinstance(item, SourceEvent) for item in added)


def test_partial_extraction_quarantines_with_each_bounded_reason(
    monkeypatch: MonkeyPatch,
) -> None:
    document = _document(
        b"""
        <main>
          <h1>Schedule</h1>
          <button aria-controls="missing">Details</button>
          <table><tr><td>Event</td><td>Date</td></tr><tr><td>Drop</td></tr></table>
        </main>
        """
    )
    extraction = StructuredExtractor().extract(document)
    source = _source(status=SourceStatus.ELIGIBLE)
    run = _run(source)
    session, added = _fake_session(monkeypatch)

    result = SourceVersionService(session, clock=lambda: OBSERVED_AT).record(
        source=source,
        document=document,
        extraction=extraction,
        ingestion_run=run,
    )

    assert result.extraction_status is ExtractionStatus.INCOMPLETE
    assert result.reason_codes == (
        ExtractionFailureReason.MISSING_EXPANDABLE_CONTENT,
        ExtractionFailureReason.MALFORMED_TABLE,
    )
    assert source.status is SourceStatus.QUARANTINED
    assert run.status is IngestionStatus.FAILED
    assert run.bounded_error_code == "missing_expandable_content"
    events = [item for item in added if isinstance(item, SourceEvent)]
    assert [event.reason_code for event in events] == [
        "missing_expandable_content",
        "malformed_table",
    ]
    assert all(event.rule_version == extraction.parser_version for event in events)
    assert all(event.evidence_ids == [] for event in events)


def test_unreadable_pdf_creates_a_failed_version_and_preserves_withdrawal(
    monkeypatch: MonkeyPatch,
) -> None:
    document = _document(b"not a PDF", MediaType.PDF)
    extraction = StructuredExtractor().extract(document)
    source = _source(MediaType.PDF, status=SourceStatus.WITHDRAWN)
    session, added = _fake_session(monkeypatch)

    result = SourceVersionService(session, clock=lambda: OBSERVED_AT).record(
        source=source,
        document=document,
        extraction=extraction,
    )

    assert result.extraction_status is ExtractionStatus.FAILED
    assert result.quarantined is True
    assert result.reason_codes == (ExtractionFailureReason.UNREADABLE_PDF,)
    assert result.version.content == ""
    assert source.status is SourceStatus.WITHDRAWN
    event = next(item for item in added if isinstance(item, SourceEvent))
    assert event.reason_code == "unreadable_pdf"


def test_identical_content_reuses_the_existing_version_without_updating_it(
    monkeypatch: MonkeyPatch,
) -> None:
    content = b"<main><h1>Policy</h1><p>A supported condition.</p></main>"
    document = _document(content)
    extraction = StructuredExtractor().extract(document)
    source = _source()
    original_fetched_at = datetime(2029, 12, 1, tzinfo=UTC)
    existing = SourceVersion(
        id=uuid4(),
        source_id=source.id,
        content_sha256=sha256(content).hexdigest(),
        content=content.decode(),
        fetched_at=original_fetched_at,
        extraction_status=ExtractionStatus.COMPLETE,
        parser_version=extraction.parser_version,
    )
    run = _run(source)
    session, added = _fake_session(monkeypatch, existing=existing)

    result = SourceVersionService(session, clock=lambda: OBSERVED_AT).record(
        source=source,
        document=document,
        extraction=extraction,
        ingestion_run=run,
    )

    assert result.created is False
    assert result.version is existing
    assert existing.fetched_at == original_fetched_at
    assert not any(isinstance(item, SourceVersion) for item in added)
    assert run.status is IngestionStatus.SUCCEEDED
    assert run.version_id == existing.id


def test_complete_material_change_suspends_previously_eligible_source(
    monkeypatch: MonkeyPatch,
) -> None:
    prior = SourceVersion(
        id=uuid4(),
        source_id=uuid4(),
        content_sha256="a" * 64,
        content="old public content",
        fetched_at=OBSERVED_AT - timedelta(days=1),
        extraction_status=ExtractionStatus.COMPLETE,
        parser_version="structured-extractor-v1",
    )
    source = _source(status=SourceStatus.ELIGIBLE)
    prior.source_id = source.id
    source.versions.append(prior)
    document = _document(b"<main><h1>Changed policy</h1><p>New condition.</p></main>")
    extraction = StructuredExtractor().extract(document)
    session, added = _fake_session(monkeypatch)

    result = SourceVersionService(session, clock=lambda: OBSERVED_AT).record(
        source=source,
        document=document,
        extraction=extraction,
    )

    assert result.created is True
    assert result.extraction_status is ExtractionStatus.COMPLETE
    assert source.status is SourceStatus.QUARANTINED
    event = next(item for item in added if isinstance(item, SourceEvent))
    assert event.reason_code == "material_change_pending"
    assert event.event_type is SourceEventType.CHANGE


def test_same_hash_with_a_different_parser_result_fails_closed(
    monkeypatch: MonkeyPatch,
) -> None:
    content = b"<main><h1>Policy</h1><p>A supported condition.</p></main>"
    document = _document(content)
    extraction = StructuredExtractor().extract(document)
    source = _source()
    existing = SourceVersion(
        id=uuid4(),
        source_id=source.id,
        content_sha256=sha256(content).hexdigest(),
        content=content.decode(),
        fetched_at=OBSERVED_AT,
        extraction_status=ExtractionStatus.COMPLETE,
        parser_version="different-parser-v1",
    )
    session, added = _fake_session(monkeypatch, existing=existing)

    with pytest.raises(VersionConflictError, match="immutable content hash"):
        SourceVersionService(session, clock=lambda: OBSERVED_AT).record(
            source=source,
            document=document,
            extraction=extraction,
        )

    assert added == []
    assert source.status is SourceStatus.CANDIDATE


def test_mismatched_source_and_document_are_rejected_before_persistence(
    monkeypatch: MonkeyPatch,
) -> None:
    document = _document(b"<main><h1>Policy</h1></main>")
    extraction = StructuredExtractor().extract(document)
    source = _source()
    source.canonical_url = "https://www.pnw.edu/different/"
    session, added = _fake_session(monkeypatch)

    with pytest.raises(VersionInputError, match="canonical URLs"):
        SourceVersionService(session).record(
            source=source,
            document=document,
            extraction=extraction,
        )

    assert added == []


def test_attached_source_version_cannot_be_updated() -> None:
    version = SourceVersion(
        id=uuid4(),
        source_id=uuid4(),
        content_sha256="a" * 64,
        content="original",
        fetched_at=OBSERVED_AT,
        extraction_status=ExtractionStatus.COMPLETE,
        parser_version="parser-v1",
    )
    make_transient_to_detached(version)
    session = Session()
    session.add(version)
    version.content = "changed"

    with pytest.raises(ImmutableVersionError, match="cannot be updated"):
        session.flush()


def test_attached_source_version_cannot_be_deleted() -> None:
    version = SourceVersion(
        id=uuid4(),
        source_id=uuid4(),
        content_sha256="a" * 64,
        content="original",
        fetched_at=OBSERVED_AT,
        extraction_status=ExtractionStatus.COMPLETE,
        parser_version="parser-v1",
    )
    make_transient_to_detached(version)
    session = Session()
    session.add(version)
    session.delete(version)

    with pytest.raises(ImmutableVersionError, match="cannot be deleted"):
        session.flush()
