"""PostgreSQL integration coverage for retrieval and final answer authorization."""

from __future__ import annotations

import hashlib
import os
import threading
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import numpy as np
import pytest
from app.config import DatabaseRole, Settings
from app.ingestion.refresh import LifecycleResult, SourceRefreshService
from app.models.enums import (
    ConflictStatus,
    ExtractionStatus,
    MediaType,
    QualificationStatus,
    SourceStatus,
)
from app.models.guidance import Conflict, conflict_evidence_blocks
from app.models.operations import SourceEvent
from app.models.sources import (
    Applicability,
    Embedding,
    EvidenceBlock,
    Qualification,
    Source,
    SourceVersion,
)
from app.retrieval.authorization import (
    AuthorizationFailureReason,
    AuthorizationPublishResult,
    EvidenceAuthorizationReference,
    FinalAnswerAuthorizer,
)
from app.retrieval.embeddings import EMBEDDING_DIMENSIONS, MODEL_NAME, TemporaryQueryVector
from app.retrieval.search import EvidenceSearch, RetrievalCandidates, RetrievalScope
from app.testing.embedding import DeterministicEmbeddingService, deterministic_vector
from numpy.typing import NDArray
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration

NOW = datetime(2030, 1, 16, 13, tzinfo=UTC)
TOPIC = "t061_retrieval_authorization"
INSTITUTION = "Purdue University Northwest"
DATABASE_INTEGRATION_ENABLED = os.environ.get("DATABASE_INTEGRATION_TESTS") == "true"
requires_database = pytest.mark.skipif(
    not DATABASE_INTEGRATION_ENABLED,
    reason="run through the Compose test-retrieval-authorization service",
)


@dataclass(frozen=True, slots=True)
class SeededEvidence:
    source_id: UUID
    version_id: UUID
    evidence_id: UUID

    @property
    def reference(self) -> EvidenceAuthorizationReference:
        return EvidenceAuthorizationReference(
            evidence_id=self.evidence_id,
            version_id=self.version_id,
        )


@dataclass(slots=True)
class DatabaseHarness:
    migration_engine: Engine
    governance_engine: Engine
    runtime_engine: Engine
    source_ids: set[UUID] = field(default_factory=set)
    conflict_ids: set[UUID] = field(default_factory=set)

    def track_source(self, source_id: UUID) -> None:
        self.source_ids.add(source_id)

    def track_conflict(self, conflict_id: UUID) -> None:
        self.conflict_ids.add(conflict_id)

    def cleanup(self) -> None:
        if not self.source_ids and not self.conflict_ids:
            return
        with Session(self.migration_engine) as session, session.begin():
            version_ids = select(SourceVersion.id).where(
                SourceVersion.source_id.in_(tuple(self.source_ids))
            )
            evidence_ids = select(EvidenceBlock.id).where(
                EvidenceBlock.version_id.in_(version_ids)
            )
            session.execute(
                delete(conflict_evidence_blocks).where(
                    conflict_evidence_blocks.c.evidence_block_id.in_(evidence_ids)
                )
            )
            if self.conflict_ids:
                session.execute(delete(Conflict).where(Conflict.id.in_(tuple(self.conflict_ids))))
            session.execute(delete(SourceEvent).where(SourceEvent.source_id.in_(self.source_ids)))
            session.execute(delete(Embedding).where(Embedding.block_id.in_(evidence_ids)))
            session.execute(delete(Applicability).where(Applicability.version_id.in_(version_ids)))
            session.execute(delete(Qualification).where(Qualification.version_id.in_(version_ids)))
            session.execute(delete(EvidenceBlock).where(EvidenceBlock.id.in_(evidence_ids)))
            session.execute(delete(SourceVersion).where(SourceVersion.id.in_(version_ids)))
            session.execute(delete(Source).where(Source.id.in_(self.source_ids)))


def _normalized(values: Sequence[float] | NDArray[np.float32]) -> NDArray[np.float32]:
    vector = np.asarray(values, dtype=np.float32)
    norm = float(np.linalg.norm(vector))
    assert vector.shape == (EMBEDDING_DIMENSIONS,)
    assert np.isfinite(norm) and norm > 0
    return np.ascontiguousarray(vector / norm, dtype=np.float32)


def _orthogonal(vector: NDArray[np.float32]) -> NDArray[np.float32]:
    candidate = np.roll(vector, 1)
    candidate = candidate - (float(np.dot(candidate, vector)) * vector)
    return _normalized(candidate)


def _source(
    harness: DatabaseHarness,
    session: Session,
    *,
    label: str,
    status: SourceStatus = SourceStatus.ELIGIBLE,
) -> Source:
    source = Source(
        id=uuid4(),
        canonical_url=f"https://www.pnw.edu/__test__/t061/{label}-{uuid4()}/",
        title=f"T061 synthetic {label}",
        media_type=MediaType.HTML,
        status=status,
    )
    harness.track_source(source.id)
    session.add(source)
    session.flush()
    return source


def _seed_version(
    session: Session,
    *,
    source: Source,
    text: str,
    vector: NDArray[np.float32],
    fetched_at: datetime = NOW - timedelta(hours=2),
    checked_at: datetime = NOW - timedelta(hours=1),
    valid_until: datetime = NOW + timedelta(hours=12),
    qualification_status: QualificationStatus = QualificationStatus.PASSED,
    campus: str | None = "Hammond",
    anchor: str | None = "fixture",
) -> SeededEvidence:
    version = SourceVersion(
        id=uuid4(),
        source_id=source.id,
        content_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        content=text,
        fetched_at=fetched_at,
        extraction_status=ExtractionStatus.COMPLETE,
        parser_version="t061-synthetic-v1",
    )
    block = EvidenceBlock(
        id=uuid4(),
        version_id=version.id,
        ordinal=0,
        heading_path=["T061 retrieval authority"],
        page=None,
        anchor=anchor,
        text=text,
        structured_content={"type": "synthetic_test_evidence"},
        topic_key=TOPIC,
        scope={},
    )
    applicability = Applicability(
        version_id=version.id,
        topic=TOPIC,
        institution=INSTITUTION,
        campus=campus,
        evidence_block_ids=[block.id],
    )
    qualification = Qualification(
        id=uuid4(),
        version_id=version.id,
        rule_version="t061-qualification-v1",
        checked_at=checked_at,
        valid_until=valid_until,
        status=qualification_status,
        check_results={"synthetic": {"passed": qualification_status is QualificationStatus.PASSED}},
        provenance_evidence={"synthetic": True},
        applicability_evidence={"synthetic": True},
    )
    embedding = Embedding(
        block_id=block.id,
        model_revision=MODEL_NAME,
        dimensions=EMBEDDING_DIMENSIONS,
        vector=vector.tolist(),
    )
    session.add_all((version, block, applicability, qualification, embedding))
    session.flush()
    return SeededEvidence(source.id, version.id, block.id)


def _add_later_failure(session: Session, seeded: SeededEvidence) -> None:
    failed_at = NOW - timedelta(minutes=30)
    session.add(
        Qualification(
            id=uuid4(),
            version_id=seeded.version_id,
            rule_version="t061-qualification-v1",
            checked_at=failed_at,
            valid_until=failed_at,
            status=QualificationStatus.FAILED,
            check_results={"synthetic": {"passed": False}},
            provenance_evidence={"synthetic": True},
            applicability_evidence={"synthetic": True},
        )
    )


def _add_conflict(
    harness: DatabaseHarness,
    session: Session,
    *,
    evidence_ids: Sequence[UUID],
) -> Conflict:
    conflict = Conflict(
        id=uuid4(),
        topic_key=TOPIC,
        scope={"campus": "Hammond"},
        status=ConflictStatus.UNRESOLVED,
        detected_at=NOW - timedelta(minutes=20),
        resolved_at=None,
        supersession_evidence_ids=[],
    )
    harness.track_conflict(conflict.id)
    session.add(conflict)
    session.flush()
    session.execute(
        conflict_evidence_blocks.insert(),
        tuple(
            {"conflict_id": conflict.id, "evidence_block_id": evidence_id}
            for evidence_id in evidence_ids
        ),
    )
    return conflict


def _search(
    engine: Engine,
    *,
    query: str,
    query_vector: TemporaryQueryVector,
    include_unresolved_conflicts: bool = False,
) -> RetrievalCandidates:
    with Session(engine) as session:
        return EvidenceSearch(session, model_revision=MODEL_NAME).search(
            query=query,
            query_vector=query_vector,
            scope=RetrievalScope(
                topic=TOPIC,
                institution=INSTITUTION,
                campus="Hammond",
            ),
            observed_at=NOW,
            include_unresolved_conflicts=include_unresolved_conflicts,
        )


@pytest.fixture
def database() -> Iterator[DatabaseHarness]:
    if not DATABASE_INTEGRATION_ENABLED:
        pytest.skip("run through the Compose test-retrieval-authorization service")
    settings = Settings()
    harness = DatabaseHarness(
        migration_engine=create_engine(settings.database_url(DatabaseRole.MIGRATION)),
        governance_engine=create_engine(settings.database_url(DatabaseRole.GOVERNANCE)),
        runtime_engine=create_engine(settings.database_url(DatabaseRole.RUNTIME)),
    )
    try:
        yield harness
    finally:
        harness.cleanup()
        harness.runtime_engine.dispose()
        harness.governance_engine.dispose()
        harness.migration_engine.dispose()


@requires_database
def test_qualification_scope_status_and_conflict_filters_fail_closed(
    database: DatabaseHarness,
) -> None:
    query_vector = _normalized(deterministic_vector("qualification authority filter"))
    with Session(database.migration_engine) as session, session.begin():
        valid = _seed_version(
            session,
            source=_source(database, session, label="valid"),
            text="The synthetic authority rule is currently usable.",
            vector=query_vector,
        )
        _seed_version(
            session,
            source=_source(database, session, label="expired"),
            text="Expired synthetic authority evidence.",
            vector=query_vector,
            checked_at=NOW - timedelta(hours=24),
            valid_until=NOW,
        )
        later_failure = _seed_version(
            session,
            source=_source(database, session, label="latest-failed"),
            text="Synthetic evidence with a later failed check.",
            vector=query_vector,
        )
        _add_later_failure(session, later_failure)
        _seed_version(
            session,
            source=_source(
                database,
                session,
                label="quarantined",
                status=SourceStatus.QUARANTINED,
            ),
            text="Quarantined synthetic authority evidence.",
            vector=query_vector,
        )
        _seed_version(
            session,
            source=_source(database, session, label="wrong-campus"),
            text="Westville-only synthetic authority evidence.",
            vector=query_vector,
            campus="Westville",
        )
        conflicted = _seed_version(
            session,
            source=_source(database, session, label="conflicted"),
            text="Disputed synthetic authority evidence.",
            vector=query_vector,
        )
        _add_conflict(database, session, evidence_ids=(conflicted.evidence_id,))

    temporary = TemporaryQueryVector(query_vector)
    try:
        candidates = _search(
            database.runtime_engine,
            query="synthetic authority",
            query_vector=temporary,
        )
        diagnostic = _search(
            database.runtime_engine,
            query="synthetic authority",
            query_vector=temporary,
            include_unresolved_conflicts=True,
        )
    finally:
        temporary.clear()

    assert {item.evidence_id for item in candidates.vector} == {valid.evidence_id}
    assert {item.evidence_id for item in diagnostic.vector} == {
        valid.evidence_id,
        conflicted.evidence_id,
    }
    assert temporary.cleared


@requires_database
def test_exact_pgvector_full_text_fusion_and_query_vector_nonpersistence(
    database: DatabaseHarness,
) -> None:
    query = "cobalt lantern"
    query_values = _normalized(deterministic_vector(query))
    orthogonal = _orthogonal(query_values)
    near_values = _normalized(query_values + (0.35 * orthogonal))
    with Session(database.migration_engine) as session, session.begin():
        dual = _seed_version(
            session,
            source=_source(database, session, label="dual-channel"),
            text="The cobalt lantern is the synthetic dual-channel marker.",
            vector=query_values,
        )
        text_only = _seed_version(
            session,
            source=_source(database, session, label="text-channel"),
            text="Use the cobalt lantern phrase for full-text matching.",
            vector=orthogonal,
        )
        vector_only = _seed_version(
            session,
            source=_source(database, session, label="vector-channel"),
            text="A semantic-only synthetic marker without the searched words.",
            vector=near_values,
        )
        embedding_count_before = session.scalar(select(func.count()).select_from(Embedding))

    service = DeterministicEmbeddingService()
    with service.temporary_query_vector(query) as temporary:
        candidates = _search(
            database.runtime_engine,
            query=query,
            query_vector=temporary,
        )
        assert [item.evidence_id for item in candidates.vector[:3]] == [
            dual.evidence_id,
            vector_only.evidence_id,
            text_only.evidence_id,
        ]
        assert candidates.vector[0].ranking_value == pytest.approx(0.0, abs=1e-6)
        assert candidates.vector[1].ranking_value < candidates.vector[2].ranking_value
        assert {item.evidence_id for item in candidates.full_text} == {
            dual.evidence_id,
            text_only.evidence_id,
        }
        assert [item.evidence_id for item in candidates.select()[:3]] == [
            dual.evidence_id,
            text_only.evidence_id,
            vector_only.evidence_id,
        ]

    assert temporary.cleared
    with Session(database.migration_engine) as session:
        assert session.scalar(select(func.count()).select_from(Embedding)) == embedding_count_before
        persisted_ids = set(
            session.scalars(
                select(Embedding.block_id).where(
                    Embedding.block_id.in_(
                        (dual.evidence_id, text_only.evidence_id, vector_only.evidence_id)
                    )
                )
            ).all()
        )
    assert persisted_ids == {dual.evidence_id, text_only.evidence_id, vector_only.evidence_id}


@requires_database
def test_newer_qualified_version_supersedes_old_retrieval_and_authorization(
    database: DatabaseHarness,
) -> None:
    query_values = _normalized(deterministic_vector("new version authority"))
    with Session(database.migration_engine) as session, session.begin():
        source = _source(database, session, label="version-supersession")
        older = _seed_version(
            session,
            source=source,
            text="The older synthetic rule says OLD-T061.",
            vector=query_values,
            fetched_at=NOW - timedelta(hours=3),
            checked_at=NOW - timedelta(hours=2),
        )
        newer = _seed_version(
            session,
            source=source,
            text="The replacement synthetic rule says NEW-T061.",
            vector=query_values,
            fetched_at=NOW - timedelta(hours=1),
            checked_at=NOW - timedelta(minutes=50),
        )

    temporary = TemporaryQueryVector(query_values)
    try:
        candidates = _search(
            database.runtime_engine,
            query="synthetic rule",
            query_vector=temporary,
        )
    finally:
        temporary.clear()
    assert {item.evidence_id for item in candidates.vector} == {newer.evidence_id}

    old_published = False

    def publish_old() -> str:
        nonlocal old_published
        old_published = True
        return "unsafe old answer"

    authorizer = FinalAnswerAuthorizer(database.runtime_engine, clock=lambda: NOW)
    old_result = authorizer.authorize_and_publish((older.reference,), publish_old)
    new_result = authorizer.authorize_and_publish((newer.reference,), lambda: "new answer")

    assert not old_result.decision.allowed
    assert old_result.decision.reason is AuthorizationFailureReason.VERSION_SUPERSEDED
    assert not old_published
    assert new_result.decision.allowed
    assert new_result.value == "new answer"


@requires_database
def test_unresolved_conflict_requires_explicit_source_backed_supersession(
    database: DatabaseHarness,
) -> None:
    query_values = _normalized(deterministic_vector("explicit supersession"))
    with Session(database.migration_engine) as session, session.begin():
        seeded = _seed_version(
            session,
            source=_source(database, session, label="conflict-supersession"),
            text="This eligible synthetic evidence explicitly supersedes the disputed rule.",
            vector=query_values,
        )
        conflict = _add_conflict(database, session, evidence_ids=(seeded.evidence_id,))
        conflict_id = conflict.id

    temporary = TemporaryQueryVector(query_values)
    try:
        blocked = _search(
            database.runtime_engine,
            query="explicit supersession",
            query_vector=temporary,
        )
        diagnostic = _search(
            database.runtime_engine,
            query="explicit supersession",
            query_vector=temporary,
            include_unresolved_conflicts=True,
        )
    finally:
        temporary.clear()
    assert blocked.vector == ()
    assert {item.evidence_id for item in diagnostic.vector} == {seeded.evidence_id}

    with Session(database.governance_engine) as session, session.begin():
        resolved = SourceRefreshService(session, clock=lambda: NOW).resolve_supersession(
            conflict_id=conflict_id,
            evidence_ids=(seeded.evidence_id,),
        )
    assert resolved.evidence_ids == (seeded.evidence_id,)

    after_resolution = TemporaryQueryVector(query_values)
    try:
        usable = _search(
            database.runtime_engine,
            query="explicit supersession",
            query_vector=after_resolution,
        )
    finally:
        after_resolution.clear()
    assert {item.evidence_id for item in usable.vector} == {seeded.evidence_id}


@requires_database
def test_withdrawal_committed_during_generation_blocks_final_publication(
    database: DatabaseHarness,
) -> None:
    query_values = _normalized(deterministic_vector("withdraw during generation"))
    with Session(database.migration_engine) as session, session.begin():
        seeded = _seed_version(
            session,
            source=_source(database, session, label="withdrawal-race"),
            text="Synthetic evidence retrieved before a governance withdrawal.",
            vector=query_values,
        )

    temporary = TemporaryQueryVector(query_values)
    try:
        retrieved = _search(
            database.runtime_engine,
            query="governance withdrawal",
            query_vector=temporary,
        ).select(maximum_evidence=1)
    finally:
        temporary.clear()
    assert len(retrieved) == 1
    assert retrieved[0].evidence_id == seeded.evidence_id

    with Session(database.governance_engine) as session, session.begin():
        withdrawal = SourceRefreshService(session, clock=lambda: NOW).withdraw(
            source_id=seeded.source_id,
            reason_code="t061_generation_withdrawal",
        )
    assert withdrawal.status is SourceStatus.WITHDRAWN

    published = False

    def publish() -> str:
        nonlocal published
        published = True
        return "unsafe answer"

    result = FinalAnswerAuthorizer(
        database.runtime_engine,
        clock=lambda: NOW,
    ).authorize_and_publish((seeded.reference,), publish)

    assert not result.decision.allowed
    assert result.decision.reason is AuthorizationFailureReason.SOURCE_WITHDRAWN
    assert result.value is None
    assert not published


@requires_database
def test_publication_lock_serializes_a_concurrent_governance_withdrawal(
    database: DatabaseHarness,
) -> None:
    query_values = _normalized(deterministic_vector("publication lock ordering"))
    with Session(database.migration_engine) as session, session.begin():
        seeded = _seed_version(
            session,
            source=_source(database, session, label="publication-lock"),
            text="Synthetic evidence used to verify publication lock ordering.",
            vector=query_values,
        )

    publisher_entered = threading.Event()
    release_publisher = threading.Event()
    withdrawal_attempted = threading.Event()
    withdrawal_finished = threading.Event()
    errors: list[BaseException] = []
    published_results: list[AuthorizationPublishResult[str]] = []
    withdrawal_results: list[LifecycleResult] = []

    def publisher() -> str:
        publisher_entered.set()
        if not release_publisher.wait(timeout=5):
            raise AssertionError("publisher release timed out")
        return "authorized answer"

    def authorize() -> None:
        try:
            published_results.append(
                FinalAnswerAuthorizer(
                    database.runtime_engine,
                    clock=lambda: NOW,
                ).authorize_and_publish((seeded.reference,), publisher)
            )
        except BaseException as exc:  # pragma: no cover - reported by the parent thread
            errors.append(exc)

    def withdraw() -> None:
        try:
            with Session(database.governance_engine) as session, session.begin():
                withdrawal_attempted.set()
                withdrawal_results.append(
                    SourceRefreshService(session, clock=lambda: NOW).withdraw(
                        source_id=seeded.source_id,
                        reason_code="t061_concurrent_withdrawal",
                    )
                )
        except BaseException as exc:  # pragma: no cover - reported by the parent thread
            errors.append(exc)
        finally:
            withdrawal_finished.set()

    authorization_thread = threading.Thread(target=authorize, daemon=True)
    authorization_thread.start()
    assert publisher_entered.wait(timeout=5)

    withdrawal_thread = threading.Thread(target=withdraw, daemon=True)
    withdrawal_thread.start()
    assert withdrawal_attempted.wait(timeout=5)
    assert not withdrawal_finished.wait(timeout=0.25)

    release_publisher.set()
    authorization_thread.join(timeout=5)
    withdrawal_thread.join(timeout=5)

    assert not authorization_thread.is_alive()
    assert not withdrawal_thread.is_alive()
    assert errors == []
    assert len(published_results) == 1
    publication = published_results[0]
    assert publication.decision.allowed
    assert publication.value == "authorized answer"
    assert len(withdrawal_results) == 1
    assert withdrawal_results[0].status is SourceStatus.WITHDRAWN
