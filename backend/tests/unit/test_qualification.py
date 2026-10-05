"""Automated source qualification checks and append-only audit behavior."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

import pytest
from app.ingestion.discovery import DiscoveredDocument
from app.ingestion.qualification import (
    ImmutableQualificationError,
    QualificationEvidence,
    QualificationFailureReason,
    SourceQualificationService,
)
from app.models.enums import (
    ConflictStatus,
    ExtractionStatus,
    MediaType,
    QualificationStatus,
    SourceStatus,
)
from app.models.guidance import Conflict
from app.models.operations import SourceEvent
from app.models.sources import (
    Applicability,
    EvidenceBlock,
    Qualification,
    Source,
    SourceVersion,
)
from pytest import MonkeyPatch
from sqlalchemy.orm import Session, make_transient_to_detached

CHECKED_AT = datetime(2030, 1, 15, 12, 0, tzinfo=UTC)
SOURCE_URL = "https://www.pnw.edu/registrar/policy/"
CONTENT = b"<main><h1>Registration</h1><p>Use the published form.</p></main>"


class _ScalarRows:
    def __init__(self, rows: Sequence[object]) -> None:
        self._rows = rows

    def unique(self) -> _ScalarRows:
        return self

    def all(self) -> Sequence[object]:
        return self._rows


def _document(*, url: str = SOURCE_URL, content: bytes = CONTENT) -> DiscoveredDocument:
    return DiscoveredDocument(
        requested_url=url,
        canonical_url=url,
        title="Registration policy",
        topics=("registration",),
        media_type=MediaType.HTML,
        content=content,
        depth=0,
        parent_url=None,
        redirect_chain=(),
    )


def _source(*, url: str = SOURCE_URL, status: SourceStatus = SourceStatus.CANDIDATE) -> Source:
    return Source(
        id=uuid4(),
        canonical_url=url,
        title="Registration policy",
        media_type=MediaType.HTML,
        status=status,
    )


def _version(
    source: Source,
    *,
    extraction_status: ExtractionStatus = ExtractionStatus.COMPLETE,
    effective_from: datetime | None = None,
    effective_to: datetime | None = None,
) -> SourceVersion:
    return SourceVersion(
        id=uuid4(),
        source_id=source.id,
        content_sha256=sha256(CONTENT).hexdigest(),
        content=CONTENT.decode(),
        fetched_at=CHECKED_AT - timedelta(minutes=10),
        effective_from=effective_from,
        effective_to=effective_to,
        extraction_status=extraction_status,
        parser_version="structured-extractor-v1",
    )


def _block(
    version: SourceVersion,
    *,
    topic: str = "registration",
    text: str = "Use the published registration form.",
    structured_content: dict[str, object] | None = None,
) -> EvidenceBlock:
    return EvidenceBlock(
        id=uuid4(),
        version_id=version.id,
        ordinal=0,
        heading_path=["Registration"],
        anchor="registration",
        text=text,
        structured_content=structured_content or {},
        topic_key=topic,
        scope={},
    )


def _applicability(
    version: SourceVersion,
    block: EvidenceBlock,
    *,
    topic: str = "registration",
    term: str | None = None,
    session: str | None = None,
) -> Applicability:
    return Applicability(
        version_id=version.id,
        topic=topic,
        institution="Purdue University Northwest",
        term=term,
        session=session,
        evidence_block_ids=[block.id],
    )


def _fake_session(
    monkeypatch: MonkeyPatch,
    *,
    blocks: Sequence[EvidenceBlock],
    applicability: Sequence[Applicability],
    conflicts: Sequence[Conflict] = (),
) -> tuple[Session, list[object]]:
    session = Session()
    added: list[object] = []
    result_queue = [blocks, applicability, conflicts]

    def scalars(_statement: object) -> _ScalarRows:
        return _ScalarRows(result_queue.pop(0))

    def add(instance: object, _warn: bool = True) -> None:
        _ = _warn
        added.append(instance)

    def flush(_objects: object = None) -> None:
        for instance in added:
            if isinstance(instance, (Qualification, SourceEvent)) and instance.id is None:
                instance.id = uuid4()

    monkeypatch.setattr(session, "scalars", scalars)
    monkeypatch.setattr(session, "add", add)
    monkeypatch.setattr(session, "flush", flush)
    return session, added


def test_all_checks_pass_and_effective_end_caps_validity(monkeypatch: MonkeyPatch) -> None:
    source = _source()
    version = _version(source, effective_to=CHECKED_AT + timedelta(hours=6))
    block = _block(version)
    applicability = _applicability(version, block)
    session, added = _fake_session(
        monkeypatch,
        blocks=(block,),
        applicability=(applicability,),
    )

    result = SourceQualificationService(session, clock=lambda: CHECKED_AT).qualify(
        source=source,
        version=version,
        evidence=QualificationEvidence(
            document=_document(),
            observed_at=CHECKED_AT - timedelta(minutes=5),
        ),
    )

    assert result.eligible is True
    assert result.reason_codes == ()
    assert result.qualification.status is QualificationStatus.PASSED
    assert result.qualification.valid_until == CHECKED_AT + timedelta(hours=6)
    assert source.status is SourceStatus.ELIGIBLE
    assert set(result.qualification.check_results) == {
        "provenance",
        "completeness",
        "applicability",
        "effective_date",
        "freshness",
        "conflict",
    }
    assert all(
        isinstance(check, dict) and check.get("passed") is True
        for check in result.qualification.check_results.values()
    )
    assert result.qualification.provenance_evidence["canonical_url"] == SOURCE_URL
    event = next(item for item in added if isinstance(item, SourceEvent))
    assert event.reason_code == "qualification_passed"
    assert event.evidence_ids == [block.id]


def test_deadline_without_explicit_term_and_session_is_quarantined(
    monkeypatch: MonkeyPatch,
) -> None:
    source = _source(status=SourceStatus.ELIGIBLE)
    version = _version(source)
    block = _block(
        version,
        topic="academic_schedule",
        text="The last day to drop is September 6.",
        structured_content={"deadline": "September 6"},
    )
    applicability = _applicability(version, block, topic="academic_schedule", term="Fall")
    session, _ = _fake_session(
        monkeypatch,
        blocks=(block,),
        applicability=(applicability,),
    )

    result = SourceQualificationService(session, clock=lambda: CHECKED_AT).qualify(
        source=source,
        version=version,
        evidence=QualificationEvidence(document=_document(), observed_at=CHECKED_AT),
    )

    assert result.eligible is False
    assert result.reason_codes == (QualificationFailureReason.DEADLINE_CONTEXT_MISSING,)
    assert result.qualification.status is QualificationStatus.FAILED
    assert result.qualification.valid_until == CHECKED_AT
    assert source.status is SourceStatus.QUARANTINED


def test_negated_deadline_disclaimer_does_not_create_a_deadline_claim(
    monkeypatch: MonkeyPatch,
) -> None:
    source = _source()
    version = _version(source)
    block = _block(
        version,
        text="This fixture supplies no real PNW procedure, deadline, or contact information.",
    )
    applicability = _applicability(version, block)
    session, _ = _fake_session(
        monkeypatch,
        blocks=(block,),
        applicability=(applicability,),
    )

    result = SourceQualificationService(session, clock=lambda: CHECKED_AT).qualify(
        source=source,
        version=version,
        evidence=QualificationEvidence(document=_document(), observed_at=CHECKED_AT),
    )

    assert result.eligible
    assert result.reason_codes == ()


def test_exactly_24_hour_old_observation_is_stale(monkeypatch: MonkeyPatch) -> None:
    source = _source()
    version = _version(source)
    block = _block(version)
    applicability = _applicability(version, block)
    session, _ = _fake_session(
        monkeypatch,
        blocks=(block,),
        applicability=(applicability,),
    )

    result = SourceQualificationService(session, clock=lambda: CHECKED_AT).qualify(
        source=source,
        version=version,
        evidence=QualificationEvidence(
            document=_document(), observed_at=CHECKED_AT - timedelta(hours=24)
        ),
    )

    assert result.reason_codes == (QualificationFailureReason.SOURCE_OBSERVATION_STALE,)
    assert source.status is SourceStatus.STALE


def test_unresolved_conflict_blocks_qualification(monkeypatch: MonkeyPatch) -> None:
    source = _source()
    version = _version(source)
    block = _block(version)
    applicability = _applicability(version, block)
    conflict = Conflict(
        id=uuid4(),
        topic_key="registration",
        scope={},
        status=ConflictStatus.UNRESOLVED,
        detected_at=CHECKED_AT,
        supersession_evidence_ids=[],
    )
    session, _ = _fake_session(
        monkeypatch,
        blocks=(block,),
        applicability=(applicability,),
        conflicts=(conflict,),
    )

    result = SourceQualificationService(session, clock=lambda: CHECKED_AT).qualify(
        source=source,
        version=version,
        evidence=QualificationEvidence(document=_document(), observed_at=CHECKED_AT),
    )

    assert result.reason_codes == (QualificationFailureReason.UNRESOLVED_CONFLICT,)
    assert result.qualification.applicability_evidence["unresolved_conflict_ids"] == [
        str(conflict.id)
    ]
    assert source.status is SourceStatus.QUARANTINED


def test_outside_boundary_and_incomplete_extraction_record_each_failure(
    monkeypatch: MonkeyPatch,
) -> None:
    outside_url = "https://example.edu/policy/"
    source = _source(url=outside_url)
    version = _version(source, extraction_status=ExtractionStatus.INCOMPLETE)
    session, added = _fake_session(monkeypatch, blocks=(), applicability=())

    result = SourceQualificationService(session, clock=lambda: CHECKED_AT).qualify(
        source=source,
        version=version,
        evidence=QualificationEvidence(
            document=_document(url=outside_url),
            observed_at=CHECKED_AT,
        ),
    )

    assert result.reason_codes == (
        QualificationFailureReason.PROVENANCE_OUTSIDE_BOUNDARY,
        QualificationFailureReason.EXTRACTION_INCOMPLETE,
        QualificationFailureReason.EVIDENCE_MISSING,
        QualificationFailureReason.APPLICABILITY_MISSING,
    )
    assert [event.reason_code for event in added if isinstance(event, SourceEvent)] == [
        reason.value for reason in result.reason_codes
    ]


def test_withdrawn_source_cannot_be_reactivated_by_a_passing_check(
    monkeypatch: MonkeyPatch,
) -> None:
    source = _source(status=SourceStatus.WITHDRAWN)
    version = _version(source)
    block = _block(version)
    applicability = _applicability(version, block)
    session, _ = _fake_session(
        monkeypatch,
        blocks=(block,),
        applicability=(applicability,),
    )

    result = SourceQualificationService(session, clock=lambda: CHECKED_AT).qualify(
        source=source,
        version=version,
        evidence=QualificationEvidence(document=_document(), observed_at=CHECKED_AT),
    )

    assert result.reason_codes == (QualificationFailureReason.SOURCE_WITHDRAWN,)
    assert source.status is SourceStatus.WITHDRAWN


def test_attached_qualification_cannot_be_updated_or_deleted() -> None:
    qualification = Qualification(
        id=uuid4(),
        version_id=uuid4(),
        rule_version="rule-v1",
        checked_at=CHECKED_AT,
        valid_until=CHECKED_AT + timedelta(hours=1),
        status=QualificationStatus.PASSED,
        check_results={},
        provenance_evidence={},
        applicability_evidence={},
    )
    make_transient_to_detached(qualification)
    session = Session()
    session.add(qualification)
    qualification.rule_version = "rule-v2"

    with pytest.raises(ImmutableQualificationError, match="cannot be updated"):
        session.flush()

    session.rollback()
    session.add(qualification)
    session.delete(qualification)
    with pytest.raises(ImmutableQualificationError, match="cannot be deleted"):
        session.flush()
