"""Final source-authority rechecks and publication-lock coverage."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.models.enums import (
    ExtractionStatus,
    MediaType,
    QualificationStatus,
    SourceStatus,
)
from app.models.sources import EvidenceBlock, Qualification, Source, SourceVersion
from app.retrieval.authorization import (
    AuthorizationFailureReason,
    EvidenceAuthorizationReference,
    FinalAnswerAuthorizer,
)
from pytest import MonkeyPatch
from sqlalchemy import create_engine
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

NOW = datetime(2030, 1, 15, 12, 0, tzinfo=UTC)
SOURCE_ID = UUID("10000000-0000-4000-8000-000000000001")
VERSION_ID = UUID("20000000-0000-4000-8000-000000000001")
EVIDENCE_ID = UUID("30000000-0000-4000-8000-000000000001")


class _ScalarRows:
    def __init__(self, rows: Sequence[object]) -> None:
        self._rows = rows

    def all(self) -> Sequence[object]:
        return self._rows


def _source(*, status: SourceStatus = SourceStatus.ELIGIBLE) -> Source:
    return Source(
        id=SOURCE_ID,
        canonical_url="https://www.pnw.edu/__test__/authorization/",
        title="Authorization fixture",
        media_type=MediaType.HTML,
        status=status,
    )


def _version(
    *,
    version_id: UUID = VERSION_ID,
    fetched_at: datetime = NOW - timedelta(hours=2),
) -> SourceVersion:
    return SourceVersion(
        id=version_id,
        source_id=SOURCE_ID,
        content_sha256="a" * 64,
        content="synthetic public content",
        fetched_at=fetched_at,
        extraction_status=ExtractionStatus.COMPLETE,
        parser_version="fixture-v1",
    )


def _evidence() -> EvidenceBlock:
    return EvidenceBlock(
        id=EVIDENCE_ID,
        version_id=VERSION_ID,
        ordinal=0,
        heading_path=["Fixture"],
        text="Synthetic guidance.",
        structured_content={},
        topic_key="synthetic_policy",
        scope={},
    )


def _qualification(
    version_id: UUID,
    *,
    checked_at: datetime,
    valid_until: datetime,
) -> Qualification:
    return Qualification(
        id=UUID(f"40000000-0000-4000-8000-{int(checked_at.timestamp()):012d}"),
        version_id=version_id,
        rule_version="fixture-rule-v1",
        checked_at=checked_at,
        valid_until=valid_until,
        status=QualificationStatus.PASSED,
        check_results={},
        provenance_evidence={},
        applicability_evidence={},
    )


def _authorizer_session(
    monkeypatch: MonkeyPatch,
    *,
    source: Source,
    versions: Sequence[SourceVersion],
    qualifications: Sequence[Qualification],
) -> tuple[FinalAnswerAuthorizer, Session, list[Select[tuple[object, ...]]]]:
    session = Session()
    evidence = _evidence()
    queues: list[Sequence[object]] = [
        (SOURCE_ID,),
        (source,),
        (evidence,),
        tuple(versions),
        tuple(qualifications),
    ]
    statements: list[Select[tuple[object, ...]]] = []

    def scalars(statement: Select[tuple[object, ...]]) -> _ScalarRows:
        statements.append(statement)
        return _ScalarRows(queues.pop(0))

    def scalar(statement: object) -> object | None:
        if isinstance(statement, Select):
            statements.append(statement)
        return None

    monkeypatch.setattr(session, "scalars", scalars)
    monkeypatch.setattr(session, "scalar", scalar)
    engine = create_engine("postgresql+psycopg://fixture:fixture@localhost/fixture")
    authorizer = FinalAnswerAuthorizer(engine, clock=lambda: NOW)
    monkeypatch.setattr(authorizer, "_session_factory", lambda: session)
    return authorizer, session, statements


def _reference() -> tuple[EvidenceAuthorizationReference, ...]:
    return (EvidenceAuthorizationReference(EVIDENCE_ID, VERSION_ID),)


def test_authorized_answer_is_published_while_source_lock_transaction_is_active(
    monkeypatch: MonkeyPatch,
) -> None:
    version = _version()
    qualification = _qualification(
        VERSION_ID,
        checked_at=NOW - timedelta(hours=1),
        valid_until=NOW + timedelta(hours=23),
    )
    authorizer, session, statements = _authorizer_session(
        monkeypatch,
        source=_source(),
        versions=(version,),
        qualifications=(qualification,),
    )

    def publish() -> str:
        assert session.in_transaction()
        return "published"

    result = authorizer.authorize_and_publish(_reference(), publish)

    assert result.decision.allowed is True
    assert result.value == "published"
    source_lock_sql = str(
        statements[1].compile(
            dialect=postgresql.dialect()  # type: ignore[no-untyped-call]
        )
    )
    assert "lock_sources_for_answer" in source_lock_sql


def test_exact_valid_until_boundary_blocks_publication(monkeypatch: MonkeyPatch) -> None:
    version = _version()
    qualification = _qualification(
        VERSION_ID,
        checked_at=NOW - timedelta(hours=24),
        valid_until=NOW,
    )
    authorizer, _, _ = _authorizer_session(
        monkeypatch,
        source=_source(),
        versions=(version,),
        qualifications=(qualification,),
    )
    published = False

    def publish() -> str:
        nonlocal published
        published = True
        return "unsafe"

    result = authorizer.authorize_and_publish(_reference(), publish)

    assert result.decision.allowed is False
    assert result.decision.reason is AuthorizationFailureReason.VERSION_EXPIRED
    assert result.value is None
    assert published is False


def test_newer_qualified_version_supersedes_older_cited_version(
    monkeypatch: MonkeyPatch,
) -> None:
    newer_id = UUID("20000000-0000-4000-8000-000000000002")
    older = _version()
    newer = _version(version_id=newer_id, fetched_at=NOW - timedelta(minutes=30))
    older_qualification = _qualification(
        VERSION_ID,
        checked_at=NOW - timedelta(hours=2),
        valid_until=NOW + timedelta(hours=22),
    )
    newer_qualification = _qualification(
        newer_id,
        checked_at=NOW - timedelta(minutes=20),
        valid_until=NOW + timedelta(hours=23),
    )
    authorizer, _, _ = _authorizer_session(
        monkeypatch,
        source=_source(),
        versions=(older, newer),
        qualifications=(older_qualification, newer_qualification),
    )

    result = authorizer.authorize_and_publish(_reference(), lambda: "unsafe")

    assert result.decision.allowed is False
    assert result.decision.reason is AuthorizationFailureReason.VERSION_SUPERSEDED


def test_committed_withdrawal_state_blocks_publication_before_evidence_checks(
    monkeypatch: MonkeyPatch,
) -> None:
    authorizer, _, _ = _authorizer_session(
        monkeypatch,
        source=_source(status=SourceStatus.WITHDRAWN),
        versions=(),
        qualifications=(),
    )

    result = authorizer.authorize_and_publish(_reference(), lambda: "unsafe")

    assert result.decision.allowed is False
    assert result.decision.reason is AuthorizationFailureReason.SOURCE_WITHDRAWN
