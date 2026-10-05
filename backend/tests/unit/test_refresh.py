"""Daily refresh, expiry, change, withdrawal, and restoration lifecycle tests."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

from app.ingestion import refresh as refresh_module
from app.ingestion.refresh import RefreshFailureReason, SourceRefreshService
from app.models.enums import (
    ConflictStatus,
    ExtractionStatus,
    MediaType,
    SourceEventType,
    SourceStatus,
)
from app.models.guidance import Conflict
from app.models.operations import SourceEvent
from app.models.sources import EvidenceBlock, Source, SourceVersion
from app.retrieval.authorization import AuthorizationDecision
from pytest import MonkeyPatch
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

NOW = datetime(2030, 1, 15, 12, 0, tzinfo=UTC)
SOURCE_ID = UUID("10000000-0000-4000-8000-000000000001")
OLD_VERSION_ID = UUID("20000000-0000-4000-8000-000000000001")
NEW_VERSION_ID = UUID("20000000-0000-4000-8000-000000000002")
EVIDENCE_ID = UUID("30000000-0000-4000-8000-000000000001")


class _ScalarRows:
    def __init__(self, rows: Sequence[object]) -> None:
        self._rows = rows

    def all(self) -> Sequence[object]:
        return self._rows


class _Rows:
    def __init__(self, rows: Sequence[object]) -> None:
        self._rows = rows

    def all(self) -> Sequence[object]:
        return self._rows


def _source(status: SourceStatus = SourceStatus.ELIGIBLE) -> Source:
    return Source(
        id=SOURCE_ID,
        canonical_url="https://www.pnw.edu/__test__/refresh/",
        title="Refresh fixture",
        media_type=MediaType.HTML,
        status=status,
    )


def _version(version_id: UUID, digest: str) -> SourceVersion:
    return SourceVersion(
        id=version_id,
        source_id=SOURCE_ID,
        content_sha256=digest * 64,
        content="synthetic public content",
        fetched_at=NOW,
        extraction_status=ExtractionStatus.COMPLETE,
        parser_version="fixture-v1",
    )


def _session(
    monkeypatch: MonkeyPatch,
    *,
    scalar_batches: Sequence[Sequence[object]],
    scalar_values: Sequence[object | None] = (),
) -> tuple[Session, list[object], list[Select[tuple[object, ...]]]]:
    session = Session()
    batches = list(scalar_batches)
    values = list(scalar_values)
    added: list[object] = []
    statements: list[Select[tuple[object, ...]]] = []

    def scalars(statement: Select[tuple[object, ...]]) -> _ScalarRows:
        statements.append(statement)
        return _ScalarRows(batches.pop(0))

    def scalar(_statement: object) -> object | None:
        return values.pop(0)

    def add(instance: object, _warn: bool = True) -> None:
        del _warn
        added.append(instance)

    def flush(_objects: object = None) -> None:
        return None

    monkeypatch.setattr(session, "scalars", scalars)
    monkeypatch.setattr(session, "scalar", scalar)
    monkeypatch.setattr(session, "add", add)
    monkeypatch.setattr(session, "flush", flush)
    return session, added, statements


def test_sources_become_due_at_the_exact_daily_boundary(monkeypatch: MonkeyPatch) -> None:
    session, _, statements = _session(monkeypatch, scalar_batches=((),))

    result = SourceRefreshService(session, clock=lambda: NOW).due_sources()

    assert result == ()
    compiled = statements[0].compile(
        dialect=postgresql.dialect()  # type: ignore[no-untyped-call]
    )
    assert NOW - timedelta(hours=24) in compiled.params.values()
    assert "max(qualifications.checked_at)" in str(compiled)
    assert "sources.status !=" in str(compiled)


def test_expired_eligible_source_is_locked_and_marked_stale(monkeypatch: MonkeyPatch) -> None:
    source = _source()
    session, added, statements = _session(
        monkeypatch,
        scalar_batches=((source,), (EVIDENCE_ID,)),
    )

    results = SourceRefreshService(session, clock=lambda: NOW).expire_due()

    assert source.status is SourceStatus.STALE
    assert results[0].reason_code is RefreshFailureReason.QUALIFICATION_EXPIRED.value
    event = next(item for item in added if isinstance(item, SourceEvent))
    assert event.event_type is SourceEventType.CHANGE
    assert event.reason_code == "qualification_expired"
    source_lock_sql = str(
        statements[0].compile(
            dialect=postgresql.dialect()  # type: ignore[no-untyped-call]
        )
    )
    assert "FOR UPDATE" in source_lock_sql


def test_material_change_blocks_old_evidence_until_requalification(
    monkeypatch: MonkeyPatch,
) -> None:
    source = _source()
    old_version = _version(OLD_VERSION_ID, "a")
    new_version = _version(NEW_VERSION_ID, "b")
    session, added, _ = _session(
        monkeypatch,
        scalar_batches=((old_version, new_version), (EVIDENCE_ID,)),
        scalar_values=(source,),
    )

    result = SourceRefreshService(session, clock=lambda: NOW).mark_material_change(
        source_id=SOURCE_ID,
        previous_version_id=OLD_VERSION_ID,
        replacement_version_id=NEW_VERSION_ID,
    )

    assert result.status is SourceStatus.QUARANTINED
    assert source.status is SourceStatus.QUARANTINED
    event = next(item for item in added if isinstance(item, SourceEvent))
    assert event.reason_code == "material_change_pending"
    assert event.evidence_ids == [EVIDENCE_ID]


def test_failed_refresh_immediately_blocks_previously_eligible_source(
    monkeypatch: MonkeyPatch,
) -> None:
    source = _source()
    session, added, _ = _session(
        monkeypatch,
        scalar_batches=((EVIDENCE_ID,),),
        scalar_values=(source,),
    )

    result = SourceRefreshService(session, clock=lambda: NOW).record_refresh_failure(
        source_id=SOURCE_ID,
        reason_code="fetch_timeout",
    )

    assert result.status is SourceStatus.STALE
    assert source.status is SourceStatus.STALE
    event = next(item for item in added if isinstance(item, SourceEvent))
    assert event.reason_code == "fetch_timeout"


def test_withdrawal_and_restore_require_fresh_requalification(monkeypatch: MonkeyPatch) -> None:
    source = _source()
    session, added, _ = _session(
        monkeypatch,
        scalar_batches=((EVIDENCE_ID,), (EVIDENCE_ID,)),
        scalar_values=(source, source),
    )
    service = SourceRefreshService(session, clock=lambda: NOW)

    withdrawn = service.withdraw(source_id=SOURCE_ID, reason_code="policy_removed")
    restored = service.restore(source_id=SOURCE_ID)

    assert withdrawn.status is SourceStatus.WITHDRAWN
    assert restored.status is SourceStatus.STALE
    assert source.status is SourceStatus.STALE
    events = [item for item in added if isinstance(item, SourceEvent)]
    assert [event.event_type for event in events] == [
        SourceEventType.WITHDRAWAL,
        SourceEventType.RESTORATION,
    ]
    assert events[1].reason_code == "restoration_requires_requalification"


def test_conflict_resolution_requires_explicit_authorized_supersession_evidence(
    monkeypatch: MonkeyPatch,
) -> None:
    version = _version(NEW_VERSION_ID, "b")
    block = EvidenceBlock(
        id=EVIDENCE_ID,
        version_id=NEW_VERSION_ID,
        ordinal=0,
        heading_path=["Synthetic policy"],
        text="This source explicitly supersedes the disputed guidance.",
        structured_content={},
        topic_key="synthetic_policy",
        scope={},
    )
    conflict = Conflict(
        id=UUID("50000000-0000-4000-8000-000000000001"),
        topic_key="synthetic_policy",
        scope={},
        status=ConflictStatus.UNRESOLVED,
        detected_at=NOW - timedelta(hours=1),
        supersession_evidence_ids=[],
    )
    session, added, _ = _session(
        monkeypatch,
        scalar_batches=((block,), (version,)),
        scalar_values=(conflict,),
    )
    monkeypatch.setattr(
        session,
        "execute",
        lambda _statement: _Rows((SimpleNamespace(id=EVIDENCE_ID, version_id=NEW_VERSION_ID),)),
    )
    monkeypatch.setattr(
        refresh_module,
        "authorize_evidence",
        lambda *_args, **_kwargs: AuthorizationDecision(allowed=True),
    )

    result = SourceRefreshService(session, clock=lambda: NOW).resolve_supersession(
        conflict_id=conflict.id,
        evidence_ids=(EVIDENCE_ID,),
    )

    assert result.evidence_ids == (EVIDENCE_ID,)
    assert conflict.status is ConflictStatus.RESOLVED
    assert conflict.resolved_at == NOW
    assert conflict.supersession_evidence_ids == [EVIDENCE_ID]
    event = next(item for item in added if isinstance(item, SourceEvent))
    assert event.reason_code == "conflict_resolved_by_supersession"
