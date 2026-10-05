"""Hybrid retrieval, structured lookup, metadata, and rank-fusion tests."""

from __future__ import annotations

from typing import cast
from uuid import UUID

import numpy as np
import pytest
from app.retrieval.embeddings import EMBEDDING_DIMENSIONS, TemporaryQueryVector
from app.retrieval.search import (
    MAX_SELECTED_EVIDENCE,
    EvidenceApplicability,
    EvidenceSearch,
    RetrievalCandidates,
    RetrievalChannel,
    RetrievalScope,
    RetrievalUnavailableError,
    RetrievedEvidence,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select


class _Mappings:
    @staticmethod
    def all() -> list[object]:
        return []


class _Result:
    @staticmethod
    def mappings() -> _Mappings:
        return _Mappings()


class _ReadOnlySession:
    def __init__(self) -> None:
        self.statements: list[Select[tuple[object, ...]]] = []

    def execute(self, statement: Select[tuple[object, ...]]) -> _Result:
        self.statements.append(statement)
        return _Result()


def _evidence(number: int, *, channel: RetrievalChannel, rank: int) -> RetrievedEvidence:
    evidence_id = UUID(f"00000000-0000-4000-8000-{number:012d}")
    return RetrievedEvidence(
        evidence_id=evidence_id,
        version_id=UUID("10000000-0000-4000-8000-000000000001"),
        source_id=UUID("20000000-0000-4000-8000-000000000001"),
        source_title="Synthetic catalog",
        canonical_url="https://catalog.pnw.edu/__test__/course/",
        ordinal=number,
        heading_path=("Courses", "SYN 35000"),
        page=None,
        anchor=f"course-{number}",
        text="Synthetic course relationship.",
        structured_content={"course_code": "SYN 35000"},
        topic_key="course_prerequisites",
        scope={"year": "2030"},
        applicability=EvidenceApplicability(
            topic="course_prerequisites",
            institution="Purdue University Northwest",
            campus="Hammond",
            student_level="Undergraduate",
            program="Synthetic Computing",
            catalog_year="2030-31",
            term=None,
            session=None,
            year="2030",
        ),
        model_revision="sentence-transformers/all-MiniLM-L6-v2",
        channel=channel,
        rank=rank,
        ranking_value=float(rank),
    )


def test_rank_fusion_deduplicates_three_channels_and_caps_selection_at_eight() -> None:
    vector = tuple(
        _evidence(number, channel=RetrievalChannel.VECTOR, rank=number + 1) for number in range(10)
    )
    full_text = (
        _evidence(9, channel=RetrievalChannel.FULL_TEXT, rank=1),
        _evidence(8, channel=RetrievalChannel.FULL_TEXT, rank=2),
    )
    structured = (_evidence(9, channel=RetrievalChannel.STRUCTURED, rank=1),)
    candidates = RetrievalCandidates(
        vector=vector,
        full_text=full_text,
        structured=structured,
    )

    selected = candidates.select()

    assert len(selected) == MAX_SELECTED_EVIDENCE
    assert len({item.evidence_id for item in selected}) == MAX_SELECTED_EVIDENCE
    assert selected[0].evidence_id == vector[9].evidence_id
    assert selected[0].channel is RetrievalChannel.VECTOR
    assert vector[7].evidence_id not in {item.evidence_id for item in selected}


def test_rank_fusion_rejects_over_limit_and_mislabeled_channel() -> None:
    item = _evidence(1, channel=RetrievalChannel.FULL_TEXT, rank=1)
    candidates = RetrievalCandidates(vector=(item,), full_text=())

    with pytest.raises(ValueError, match="between one and eight"):
        candidates.select(maximum_evidence=9)
    with pytest.raises(RetrievalUnavailableError):
        candidates.select()


def test_search_statement_combines_exact_vector_text_and_structured_course_lookup() -> None:
    values = np.zeros((EMBEDDING_DIMENSIONS,), dtype=np.float32)
    values[0] = 1.0
    query_vector = TemporaryQueryVector(values)
    session = _ReadOnlySession()
    search = EvidenceSearch(
        cast(Session, session),
        model_revision="sentence-transformers/all-MiniLM-L6-v2",
    )

    candidates = search.search(
        query="What are the prerequisites for syn-35000?",
        query_vector=query_vector,
        scope=RetrievalScope(
            topic="course_prerequisites",
            institution="Purdue University Northwest",
            campus="Hammond",
            student_level="Undergraduate",
            program="Synthetic Computing",
            catalog_year="2030-31",
            year="2030",
        ),
    )

    assert candidates.empty
    assert len(session.statements) == 1
    compiled = session.statements[0].compile(
        dialect=postgresql.dialect()  # type: ignore[no-untyped-call]
    )
    sql = str(compiled)
    assert "<=>" in sql
    assert "websearch_to_tsquery" in sql
    assert "course_relations" in sql
    assert "upper(course_relations.course_code)" in sql
    assert sql.count("UNION ALL") == 2
    assert "applicability.campus" in sql
    assert "applicability.catalog_year" in sql
    assert "evidence_blocks.scope" in sql
    assert "newer_version" in sql
    assert any(
        isinstance(value, list) and value == ["SYN 35000"] for value in compiled.params.values()
    )
