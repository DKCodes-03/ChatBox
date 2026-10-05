"""Unit coverage for source-backed prerequisite parsing and retrieval."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

import pytest
from app.academic.prerequisites import (
    InvalidPrerequisiteDataError,
    PrerequisiteOperator,
    PrerequisiteRepository,
    parse_course_relations,
)
from app.models.enums import CourseRelationType
from app.models.guidance import CourseRelation

EVIDENCE_ID = UUID("32000000-0000-4000-8000-000000000005")


def _relation(
    relation_id: int,
    related_course_code: str,
    relation: CourseRelationType,
    group_expression: dict[str, object],
    *,
    evidence_block_id: UUID = EVIDENCE_ID,
    campus: str | None = "Hammond",
    catalog_year: str | None = "2030-2031",
) -> CourseRelation:
    return CourseRelation(
        id=UUID(f"52000000-0000-4000-8000-{relation_id:012d}"),
        evidence_block_id=evidence_block_id,
        course_code="SYN 25000",
        related_course_code=related_course_code,
        relation=relation,
        group_expression=group_expression,
        campus=campus,
        catalog_year=catalog_year,
    )


def _relationships() -> tuple[CourseRelation, ...]:
    return (
        _relation(
            1,
            "SYN 11000",
            CourseRelationType.PREREQUISITE,
            {
                "operator": "or",
                "group": 1,
                "group_operator": "and",
                "minimum_grade": "C",
            },
        ),
        _relation(
            2,
            "SYN 12000",
            CourseRelationType.PREREQUISITE,
            {
                "operator": "or",
                "group": 1,
                "group_operator": "and",
                "minimum_grade": "C",
            },
        ),
        _relation(
            3,
            "SYN 13000",
            CourseRelationType.PREREQUISITE,
            {
                "operator": "or",
                "group": 2,
                "group_operator": "single",
                "minimum_grade": "C",
            },
        ),
        _relation(
            4,
            "SYN 25001",
            CourseRelationType.COREQUISITE,
            {"operator": "and", "concurrent_enrollment_allowed": True},
        ),
    )


def test_parser_preserves_alternative_groups_grades_corequisites_and_scope() -> None:
    results = parse_course_relations(_relationships())

    assert len(results) == 1
    requirements = results[0]
    assert requirements.course_code == "SYN 25000"
    assert requirements.evidence_block_id == EVIDENCE_ID
    assert requirements.campus == "Hammond"
    assert requirements.catalog_year == "2030-2031"
    assert requirements.operator is PrerequisiteOperator.OR
    assert [group.operator for group in requirements.prerequisite_groups] == [
        PrerequisiteOperator.AND,
        PrerequisiteOperator.SINGLE,
    ]
    assert [item.course_code for item in requirements.prerequisite_groups[0].requirements] == [
        "SYN 11000",
        "SYN 12000",
    ]
    assert [item.minimum_grade for item in requirements.prerequisite_groups[0].requirements] == [
        "C",
        "C",
    ]
    assert requirements.prerequisite_groups[1].requirements[0].course_code == "SYN 13000"
    assert requirements.corequisites[0].course_code == "SYN 25001"
    assert requirements.corequisites[0].concurrent_enrollment_allowed is True


def test_parser_keeps_different_catalog_and_campus_scopes_separate() -> None:
    westville_id = UUID("32000000-0000-4000-8000-000000000099")
    relations = (
        *_relationships(),
        _relation(
            99,
            "SYN 14000",
            CourseRelationType.PREREQUISITE,
            {
                "operator": "and",
                "group": 1,
                "group_operator": "single",
                "minimum_grade": "B",
            },
            evidence_block_id=westville_id,
            campus="Westville",
            catalog_year="2031-2032",
        ),
    )

    results = parse_course_relations(relations)

    assert [(item.campus, item.catalog_year, item.evidence_block_id) for item in results] == [
        ("Hammond", "2030-2031", EVIDENCE_ID),
        ("Westville", "2031-2032", westville_id),
    ]


@pytest.mark.parametrize(
    "relationships",
    [
        (
            _relation(
                10,
                "SYN 11000",
                CourseRelationType.PREREQUISITE,
                {"operator": "or", "group": 1, "group_operator": "single"},
            ),
            _relation(
                11,
                "SYN 12000",
                CourseRelationType.PREREQUISITE,
                {"operator": "and", "group": 2, "group_operator": "single"},
            ),
        ),
        (
            _relation(
                12,
                "SYN 11000",
                CourseRelationType.PREREQUISITE,
                {"operator": "or", "group": 1, "group_operator": "single"},
            ),
            _relation(
                13,
                "SYN 12000",
                CourseRelationType.PREREQUISITE,
                {"operator": "or", "group": 1, "group_operator": "single"},
            ),
        ),
        (
            _relation(
                14,
                "SYN 11000",
                CourseRelationType.PREREQUISITE,
                {"operator": "or", "group": 0, "group_operator": "single"},
            ),
        ),
    ],
)
def test_parser_fails_closed_for_ambiguous_or_invalid_group_metadata(
    relationships: tuple[CourseRelation, ...],
) -> None:
    with pytest.raises(InvalidPrerequisiteDataError) as error:
        parse_course_relations(relationships)

    assert str(error.value) == "prerequisite relationship data is invalid"


class _ScalarResult:
    def __init__(self, rows: Sequence[CourseRelation]) -> None:
        self._rows = rows

    def all(self) -> Sequence[CourseRelation]:
        return self._rows


class _ExecuteResult:
    def __init__(self, rows: Sequence[CourseRelation]) -> None:
        self._rows = rows

    def scalars(self) -> _ScalarResult:
        return _ScalarResult(self._rows)


class _FakeSession:
    def __init__(self, rows: Sequence[CourseRelation]) -> None:
        self._rows = rows
        self.statements: list[Any] = []

    def execute(self, statement: Any) -> _ExecuteResult:
        self.statements.append(statement)
        return _ExecuteResult(self._rows)


def test_repository_retrieves_only_from_explicit_eligible_evidence_scope() -> None:
    session = _FakeSession(_relationships())
    repository = PrerequisiteRepository(session)  # type: ignore[arg-type]

    results = repository.retrieve(
        course_code=" syn   25000 ",
        eligible_evidence_ids=(EVIDENCE_ID,),
        campus=" Hammond ",
        catalog_year=" 2030-2031 ",
    )

    assert len(session.statements) == 1
    assert results[0].course_code == "SYN 25000"
    assert results[0].campus == "Hammond"
    assert results[0].catalog_year == "2030-2031"

    with pytest.raises(ValueError, match="eligible evidence is required"):
        repository.retrieve(course_code="SYN 25000", eligible_evidence_ids=())

