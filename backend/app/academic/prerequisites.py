"""Parse and retrieve source-backed prerequisite relationships without flattening their logic."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.enums import CourseRelationType
from app.models.guidance import CourseRelation

MAX_COURSE_CODE_CHARACTERS = 32
MAX_SCOPE_CHARACTERS = 128
MAX_CATALOG_YEAR_CHARACTERS = 32
MAX_GRADE_CHARACTERS = 32
MAX_ELIGIBLE_EVIDENCE_IDS = 8

_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


class PrerequisiteOperator(StrEnum):
    """Logical relationship retained from eligible catalog evidence."""

    AND = "and"
    OR = "or"
    SINGLE = "single"


class InvalidPrerequisiteDataError(ValueError):
    """Structured course-relation data is incomplete, inconsistent, or unsafe to summarize."""

    def __init__(self) -> None:
        super().__init__("prerequisite relationship data is invalid")


class PrerequisiteRetrievalError(RuntimeError):
    """Prerequisite records could not be read without exposing database details."""

    def __init__(self) -> None:
        super().__init__("prerequisite retrieval is unavailable")


@dataclass(frozen=True, slots=True)
class PrerequisiteRequirement:
    """One course condition within a prerequisite group."""

    course_code: str
    minimum_grade: str | None


@dataclass(frozen=True, slots=True)
class PrerequisiteGroup:
    """One parenthesized prerequisite alternative or conjunction."""

    number: int
    operator: PrerequisiteOperator
    requirements: tuple[PrerequisiteRequirement, ...]


@dataclass(frozen=True, slots=True)
class CorequisiteRequirement:
    """A distinct corequisite and its published concurrent-enrollment rule."""

    course_code: str
    concurrent_enrollment_allowed: bool | None


@dataclass(frozen=True, slots=True)
class CoursePrerequisites:
    """Relationships supported by one evidence block for one catalog/campus scope."""

    course_code: str
    evidence_block_id: UUID
    operator: PrerequisiteOperator | None
    prerequisite_groups: tuple[PrerequisiteGroup, ...]
    corequisites: tuple[CorequisiteRequirement, ...]
    campus: str | None
    catalog_year: str | None


@dataclass(frozen=True, slots=True)
class _ParsedPrerequisite:
    group_number: int
    expression_operator: PrerequisiteOperator
    group_operator: PrerequisiteOperator
    requirement: PrerequisiteRequirement


_ScopeKey = tuple[UUID, str, str | None, str | None]


def _clean_text(
    value: object,
    *,
    maximum: int,
    uppercase: bool = False,
    optional: bool = False,
) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise InvalidPrerequisiteDataError()
    cleaned = " ".join(value.split())
    if not cleaned or len(cleaned) > maximum or _CONTROL_RE.search(value):
        raise InvalidPrerequisiteDataError()
    return cleaned.upper() if uppercase else cleaned


def _course_code(value: object) -> str:
    return cast(
        str,
        _clean_text(value, maximum=MAX_COURSE_CODE_CHARACTERS, uppercase=True),
    )


def _scope_text(value: object, *, catalog_year: bool = False) -> str | None:
    return _clean_text(
        value,
        maximum=MAX_CATALOG_YEAR_CHARACTERS if catalog_year else MAX_SCOPE_CHARACTERS,
        optional=True,
    )


def _expression(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise InvalidPrerequisiteDataError()
    return cast(Mapping[str, object], value)


def _operator(value: object) -> PrerequisiteOperator:
    if not isinstance(value, str):
        raise InvalidPrerequisiteDataError()
    try:
        return PrerequisiteOperator(value.strip().lower())
    except ValueError:
        raise InvalidPrerequisiteDataError() from None


def _optional_grade(value: object) -> str | None:
    return _clean_text(value, maximum=MAX_GRADE_CHARACTERS, optional=True)


def _parse_prerequisite(relation: CourseRelation) -> _ParsedPrerequisite:
    expression = _expression(relation.group_expression)
    group_number = expression.get("group")
    if isinstance(group_number, bool) or not isinstance(group_number, int) or group_number < 1:
        raise InvalidPrerequisiteDataError()
    return _ParsedPrerequisite(
        group_number=group_number,
        expression_operator=_operator(expression.get("operator")),
        group_operator=_operator(expression.get("group_operator")),
        requirement=PrerequisiteRequirement(
            course_code=_course_code(relation.related_course_code),
            minimum_grade=_optional_grade(expression.get("minimum_grade")),
        ),
    )


def _parse_corequisite(relation: CourseRelation) -> CorequisiteRequirement:
    expression = _expression(relation.group_expression)
    concurrent = expression.get("concurrent_enrollment_allowed")
    if concurrent is not None and not isinstance(concurrent, bool):
        raise InvalidPrerequisiteDataError()
    return CorequisiteRequirement(
        course_code=_course_code(relation.related_course_code),
        concurrent_enrollment_allowed=concurrent,
    )


def _assemble_scope(key: _ScopeKey, relations: Sequence[CourseRelation]) -> CoursePrerequisites:
    evidence_block_id, course_code, campus, catalog_year = key
    parsed_prerequisites: list[_ParsedPrerequisite] = []
    corequisites: list[CorequisiteRequirement] = []
    for relation in relations:
        try:
            relation_type = CourseRelationType(relation.relation)
        except ValueError:
            raise InvalidPrerequisiteDataError() from None
        if relation_type is CourseRelationType.PREREQUISITE:
            parsed_prerequisites.append(_parse_prerequisite(relation))
        elif relation_type is CourseRelationType.COREQUISITE:
            corequisites.append(_parse_corequisite(relation))
        else:
            raise InvalidPrerequisiteDataError()

    expression_operators = {item.expression_operator for item in parsed_prerequisites}
    if len(expression_operators) > 1:
        raise InvalidPrerequisiteDataError()
    expression_operator = next(iter(expression_operators), None)

    grouped: dict[int, list[_ParsedPrerequisite]] = defaultdict(list)
    for item in parsed_prerequisites:
        grouped[item.group_number].append(item)

    prerequisite_groups: list[PrerequisiteGroup] = []
    seen_prerequisites: set[str] = set()
    for group_number in sorted(grouped):
        group_items = grouped[group_number]
        group_operators = {item.group_operator for item in group_items}
        if len(group_operators) != 1:
            raise InvalidPrerequisiteDataError()
        group_operator = next(iter(group_operators))
        requirements = tuple(
            sorted(
                (item.requirement for item in group_items),
                key=lambda requirement: requirement.course_code,
            )
        )
        course_codes = {item.course_code for item in requirements}
        if len(course_codes) != len(requirements):
            raise InvalidPrerequisiteDataError()
        if group_operator is PrerequisiteOperator.SINGLE and len(requirements) != 1:
            raise InvalidPrerequisiteDataError()
        if seen_prerequisites.intersection(course_codes):
            raise InvalidPrerequisiteDataError()
        seen_prerequisites.update(course_codes)
        prerequisite_groups.append(
            PrerequisiteGroup(
                number=group_number,
                operator=group_operator,
                requirements=requirements,
            )
        )

    sorted_corequisites = tuple(sorted(corequisites, key=lambda item: item.course_code))
    if len({item.course_code for item in sorted_corequisites}) != len(sorted_corequisites):
        raise InvalidPrerequisiteDataError()
    if not prerequisite_groups and not sorted_corequisites:
        raise InvalidPrerequisiteDataError()

    return CoursePrerequisites(
        course_code=course_code,
        evidence_block_id=evidence_block_id,
        operator=expression_operator,
        prerequisite_groups=tuple(prerequisite_groups),
        corequisites=sorted_corequisites,
        campus=campus,
        catalog_year=catalog_year,
    )


def parse_course_relations(relations: Iterable[CourseRelation]) -> tuple[CoursePrerequisites, ...]:
    """Build scoped requirement trees while retaining every published logical condition."""

    grouped: dict[_ScopeKey, list[CourseRelation]] = defaultdict(list)
    for relation in relations:
        if not isinstance(relation, CourseRelation):
            raise InvalidPrerequisiteDataError()
        if not isinstance(relation.evidence_block_id, UUID):
            raise InvalidPrerequisiteDataError()
        key = (
            relation.evidence_block_id,
            _course_code(relation.course_code),
            _scope_text(relation.campus),
            _scope_text(relation.catalog_year, catalog_year=True),
        )
        grouped[key].append(relation)

    ordered_keys = sorted(
        grouped,
        key=lambda key: (key[1], key[2] or "", key[3] or "", str(key[0])),
    )
    return tuple(_assemble_scope(key, grouped[key]) for key in ordered_keys)


def _retrieval_text(
    value: object,
    *,
    maximum: int,
    uppercase: bool = False,
    optional: bool = False,
) -> str | None:
    try:
        return _clean_text(
            value,
            maximum=maximum,
            uppercase=uppercase,
            optional=optional,
        )
    except InvalidPrerequisiteDataError:
        raise ValueError("prerequisite retrieval input is invalid") from None


class PrerequisiteRepository:
    """Read course relationships only from evidence already admitted by retrieval."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def retrieve(
        self,
        *,
        course_code: str,
        eligible_evidence_ids: Iterable[UUID],
        campus: str | None = None,
        catalog_year: str | None = None,
    ) -> tuple[CoursePrerequisites, ...]:
        normalized_code = cast(
            str,
            _retrieval_text(
                course_code,
                maximum=MAX_COURSE_CODE_CHARACTERS,
                uppercase=True,
            ),
        )
        normalized_campus = _retrieval_text(
            campus,
            maximum=MAX_SCOPE_CHARACTERS,
            optional=True,
        )
        normalized_catalog_year = _retrieval_text(
            catalog_year,
            maximum=MAX_CATALOG_YEAR_CHARACTERS,
            optional=True,
        )
        evidence_ids = tuple(dict.fromkeys(eligible_evidence_ids))
        if (
            not evidence_ids
            or len(evidence_ids) > MAX_ELIGIBLE_EVIDENCE_IDS
            or any(not isinstance(evidence_id, UUID) for evidence_id in evidence_ids)
        ):
            raise ValueError("eligible evidence is required")

        statement = select(CourseRelation).where(
            CourseRelation.course_code == normalized_code,
            CourseRelation.evidence_block_id.in_(evidence_ids),
            CourseRelation.relation.in_(
                (CourseRelationType.PREREQUISITE, CourseRelationType.COREQUISITE)
            ),
        )
        if normalized_campus is not None:
            statement = statement.where(CourseRelation.campus == normalized_campus)
        if normalized_catalog_year is not None:
            statement = statement.where(CourseRelation.catalog_year == normalized_catalog_year)
        statement = statement.order_by(
            CourseRelation.evidence_block_id,
            CourseRelation.relation,
            CourseRelation.related_course_code,
        )

        try:
            relations = self._session.execute(statement).scalars().all()
        except SQLAlchemyError:
            raise PrerequisiteRetrievalError() from None
        return parse_course_relations(relations)
