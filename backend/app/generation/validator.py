"""Fail-closed validation for generated answers and their evidence citations."""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Protocol
from urllib.parse import quote, urlsplit, urlunsplit
from uuid import UUID

from app.context import ContextField
from app.generation.schemas import (
    AnswerOutcome,
    AnswerSegment,
    ReasonCode,
    SegmentKind,
    StructuredAnswer,
)
from app.retrieval.search import RetrievalScope, RetrievedEvidence

SAFE_VALIDATION_LIMITATION = (
    "I cannot provide a reliable answer from the available university information."
)
MAX_CITATIONS = 8
MIN_LEXICAL_SUPPORT = 1.0

_WORD_RE = re.compile(r"[a-z0-9]+(?:['-][a-z0-9]+)?", re.IGNORECASE)
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+|[;\n]+")
_HTML_RE = re.compile(r"<\s*/?\s*[a-z][^>]*>", re.IGNORECASE)
_MARKDOWN_LINK_RE = re.compile(r"!?\[[^\]]*]\([^)]*\)")
_URL_OR_EMAIL_RE = re.compile(r"(?:https?://|www\.|\b[\w.+-]+@[\w.-]+\.[a-z]{2,}\b)", re.I)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_DIRECTIVE_RE = re.compile(
    r"(?:^|[.!?]\s+)(?:contact|call|email|visit|submit|pay|register|appeal|drop|add)\b"
    r"|\b(?:you|students?)\s+(?:must|should|can|need(?:s)?\s+to|are\s+required\s+to)\b",
    re.IGNORECASE,
)
_SAFE_LIMITATION_RE = re.compile(
    r"\b(?:cannot|can't|unable|unknown|unclear|not\s+enough|insufficient|"
    r"do\s+not\s+have|don't\s+have|do\s+not\s+know|don't\s+know|"
    r"could\s+not\s+verify|couldn't\s+verify|"
    r"not\s+available)\b",
    re.IGNORECASE,
)

_MONTHS = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sept": 9,
    "sep": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}
_MONTH_PATTERN = "|".join(sorted(_MONTHS, key=len, reverse=True))
_NAMED_DATE_RE = re.compile(
    rf"\b(?P<month>{_MONTH_PATTERN})\.?\s+(?P<day>\d{{1,2}})(?:st|nd|rd|th)?"
    rf"(?:,?\s+(?P<year>\d{{4}}))?\b",
    re.IGNORECASE,
)
_ISO_DATE_RE = re.compile(r"\b(?P<year>\d{4})-(?P<month>\d{1,2})-(?P<day>\d{1,2})\b")
_NUMERIC_DATE_RE = re.compile(r"\b(?P<month>\d{1,2})/(?P<day>\d{1,2})(?:/(?P<year>\d{2,4}))?\b")
_COURSE_RE = re.compile(r"\b(?P<prefix>[A-Z]{2,5})\s*-?\s*(?P<number>\d{3,5}[A-Z]?)\b")
_PERCENT_RE = re.compile(r"(?<!\w)(?P<number>\d+(?:\.\d+)?)\s*%")
_CURRENCY_RE = re.compile(r"(?<!\w)\$\s*(?P<number>\d[\d,]*(?:\.\d{1,2})?)")
_TIME_RE = re.compile(
    r"\b(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<period>a\.?m\.?|p\.?m\.?)\b",
    re.IGNORECASE,
)
_GRADE_RE = re.compile(
    r"\b(?:grade\s+of\s+)?(?P<grade>[ABCDF][+-]?)\s+or\s+better\b",
    re.IGNORECASE,
)
_NUMBER_RE = re.compile(r"(?<![\w.])\d+(?:\.\d+)?(?![\w.])")
_PAST_MARKER_RE = re.compile(
    r"\b(?:past|passed|ended|expired|closed|previous|former|was|were|dated)\b",
    re.IGNORECASE,
)

_TABLE_KEYS = frozenset(
    {
        "table",
        "tables",
        "row",
        "rows",
        "column",
        "columns",
        "header",
        "headers",
        "cell",
        "cells",
        "percentage",
        "refund",
        "deadline",
    }
)
_PREREQUISITE_KEYS = frozenset(
    {
        "prerequisite",
        "prerequisites",
        "corequisite",
        "corequisites",
        "equivalent",
        "equivalence",
        "relation",
        "operator",
        "group_expression",
        "course_code",
        "related_course_code",
        "grade",
    }
)
_COURSE_PREFIX_EXCLUSIONS = frozenset(
    {
        "FALL",
        "SPRING",
        "SUMMER",
        "WINTER",
        "JAN",
        "FEB",
        "MAR",
        "APR",
        "JUN",
        "JUL",
        "AUG",
        "SEP",
        "SEPT",
        "OCT",
        "NOV",
        "DEC",
        "JANUARY",
        "FEBRUARY",
        "MARCH",
        "APRIL",
        "MAY",
        "JUNE",
        "JULY",
        "AUGUST",
        "SEPTEMBER",
        "OCTOBER",
        "NOVEMBER",
        "DECEMBER",
    }
)
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "in",
        "into",
        "is",
        "it",
        "of",
        "on",
        "that",
        "the",
        "their",
        "this",
        "to",
        "with",
        "you",
        "your",
        "first",
        "next",
        "then",
        "finally",
    }
)


class ValidationFailureCode(StrEnum):
    """Bounded validation results that contain no answer or evidence content."""

    INVALID_EVIDENCE = "invalid_evidence"
    UNKNOWN_EVIDENCE_ID = "unknown_evidence_id"
    INVALID_CITATION = "invalid_citation"
    SCOPE_MISMATCH = "scope_mismatch"
    UNSUPPORTED_CLAIM = "unsupported_claim"
    DATE_MISMATCH = "date_mismatch"
    PAST_DATE_UNLABELED = "past_date_unlabeled"
    TABLE_RELATIONSHIP_MISMATCH = "table_relationship_mismatch"
    PREREQUISITE_MISMATCH = "prerequisite_mismatch"
    UNSAFE_LIMITATION = "unsafe_limitation"
    INVALID_CLARIFICATION = "invalid_clarification"
    UNSAFE_FORMAT = "unsafe_format"


@dataclass(frozen=True, slots=True)
class EvidenceCitation:
    """Citation fields derived only from a retrieved eligible evidence block."""

    id: str
    evidence_id: UUID
    source_title: str
    url: str
    section: str | None
    page: int | None
    version_id: UUID


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """Accepted answer plus citations, or a fixed safe replacement."""

    accepted: bool
    answer: StructuredAnswer = dataclass_field(repr=False)
    citations: tuple[EvidenceCitation, ...]
    failures: tuple[ValidationFailureCode, ...]


@dataclass(frozen=True, slots=True)
class _Fact:
    kind: str
    value: str


class ClaimSupportChecker(Protocol):
    """Calibrated semantic check for paraphrases after deterministic fact checks."""

    def supports(self, claim: str, evidence: Sequence[RetrievedEvidence]) -> bool: ...


class LexicalSupportChecker:
    """Conservative claim/evidence overlap check used as the deterministic baseline."""

    def __init__(self, *, minimum_support: float = MIN_LEXICAL_SUPPORT) -> None:
        if not 0 < minimum_support <= 1:
            raise ValueError("minimum support must be greater than zero and at most one")
        self._minimum_support = minimum_support

    def supports(self, claim: str, evidence: Sequence[RetrievedEvidence]) -> bool:
        claim_tokens = _content_tokens(claim)
        if not claim_tokens:
            return False
        for item in evidence:
            for support_unit in _evidence_support_units(item):
                shared = claim_tokens & _content_tokens(support_unit)
                if len(claim_tokens) <= 3 and len(shared) == len(claim_tokens):
                    return True
                if (
                    len(claim_tokens) > 3
                    and len(shared) >= 2
                    and len(shared) / len(claim_tokens) >= self._minimum_support
                ):
                    return True
        return False


class AnswerValidator:
    """Validate an untrusted structured candidate before any student can see it."""

    def __init__(
        self,
        *,
        lexical_checker: ClaimSupportChecker | None = None,
        semantic_checker: ClaimSupportChecker | None = None,
    ) -> None:
        self._lexical_checker = lexical_checker or LexicalSupportChecker()
        self._semantic_checker = semantic_checker

    def validate(
        self,
        candidate: StructuredAnswer,
        *,
        evidence: Sequence[RetrievedEvidence],
        scope: RetrievalScope,
        observed_at: datetime | None = None,
        explicit_context: Mapping[ContextField, str] | None = None,
        known_context_values: Mapping[ContextField, Collection[str]] | None = None,
    ) -> ValidationResult:
        """Return the candidate only if its complete evidence chain validates."""

        if not isinstance(candidate, StructuredAnswer):
            raise TypeError("candidate must be a structured answer")
        if not isinstance(scope, RetrievalScope):
            raise TypeError("scope must be a retrieval scope")
        checked_at = _validated_time(observed_at)
        try:
            answer_context = _answer_context(scope, explicit_context)
            context_values = _normalize_context_values(known_context_values)
        except (TypeError, ValueError):
            return self._rejection((ValidationFailureCode.SCOPE_MISMATCH,))

        failures: list[ValidationFailureCode] = []
        evidence_index = self._index_evidence(evidence, failures)
        if candidate.outcome is AnswerOutcome.CLARIFICATION:
            self._validate_clarification(candidate, answer_context, context_values, failures)
        else:
            for segment in candidate.segments:
                self._validate_segment(
                    segment,
                    evidence_index=evidence_index,
                    scope=scope,
                    answer_context=answer_context,
                    observed_at=checked_at,
                    known_context_values=context_values,
                    failures=failures,
                )

        unique_failures = tuple(dict.fromkeys(failures))
        if unique_failures:
            return self._rejection(unique_failures)

        citations = self._citations(candidate, evidence_index, failures)
        unique_failures = tuple(dict.fromkeys(failures))
        if unique_failures:
            return self._rejection(unique_failures)
        return ValidationResult(
            accepted=True,
            answer=candidate,
            citations=citations,
            failures=(),
        )

    def _validate_segment(
        self,
        segment: AnswerSegment,
        *,
        evidence_index: Mapping[UUID, RetrievedEvidence],
        scope: RetrievalScope,
        answer_context: Mapping[ContextField, str | None],
        observed_at: datetime,
        known_context_values: Mapping[ContextField, frozenset[str]],
        failures: list[ValidationFailureCode],
    ) -> None:
        if _has_unsafe_format(segment.text):
            failures.append(ValidationFailureCode.UNSAFE_FORMAT)

        referenced: list[RetrievedEvidence] = []
        for evidence_id in segment.evidence_ids:
            item = evidence_index.get(evidence_id)
            if item is None:
                failures.append(ValidationFailureCode.UNKNOWN_EVIDENCE_ID)
                continue
            referenced.append(item)
            if not _scope_matches(item, scope):
                failures.append(ValidationFailureCode.SCOPE_MISMATCH)

        if segment.kind is SegmentKind.LIMITATION and not referenced:
            if not _is_safe_limitation(segment.text):
                failures.append(ValidationFailureCode.UNSAFE_LIMITATION)
            return
        if not referenced:
            failures.append(ValidationFailureCode.UNKNOWN_EVIDENCE_ID)
            return

        sentences = _sentences(segment.text)
        for sentence in sentences:
            sentence_facts = _facts(sentence)
            support_text = _claim_support_text(sentence, sentence_facts, observed_at.date())
            if not _facts_share_evidence(sentence_facts, referenced):
                failure = (
                    ValidationFailureCode.DATE_MISMATCH
                    if any(
                        fact.kind in {"date", "invalid_date", "invalid_time"}
                        for fact in sentence_facts
                    )
                    else ValidationFailureCode.UNSUPPORTED_CLAIM
                )
                failures.append(failure)
            if not _past_dates_are_labeled(sentence, sentence_facts, observed_at.date()):
                failures.append(ValidationFailureCode.PAST_DATE_UNLABELED)
            if not _table_relationships_match(sentence, sentence_facts, referenced):
                failures.append(ValidationFailureCode.TABLE_RELATIONSHIP_MISMATCH)
            if not _prerequisites_match(sentence, sentence_facts, referenced):
                failures.append(ValidationFailureCode.PREREQUISITE_MISMATCH)
            if not _modal_meaning_matches(sentence, referenced):
                failures.append(ValidationFailureCode.UNSUPPORTED_CLAIM)
            if _mentions_mismatched_scope(
                sentence,
                answer_context=answer_context,
                known_context_values=known_context_values,
            ):
                failures.append(ValidationFailureCode.SCOPE_MISMATCH)
            if not self._claim_is_supported(support_text, referenced):
                failures.append(ValidationFailureCode.UNSUPPORTED_CLAIM)

        for item in referenced:
            if not any(
                _facts_share_evidence(_facts(sentence), (item,))
                and self._claim_is_supported(
                    _claim_support_text(sentence, _facts(sentence), observed_at.date()),
                    (item,),
                )
                for sentence in sentences
            ):
                failures.append(ValidationFailureCode.INVALID_CITATION)

    def _claim_is_supported(
        self,
        claim: str,
        evidence: Sequence[RetrievedEvidence],
    ) -> bool:
        try:
            lexical_support = self._lexical_checker.supports(claim, evidence)
            if self._semantic_checker is None:
                return lexical_support
            semantic_support = self._semantic_checker.supports(claim, evidence)
            return lexical_support or semantic_support
        except Exception:
            return False

    @staticmethod
    def _validate_clarification(
        candidate: StructuredAnswer,
        answer_context: Mapping[ContextField, str | None],
        known_context_values: Mapping[ContextField, frozenset[str]],
        failures: list[ValidationFailureCode],
    ) -> None:
        clarification = candidate.clarification
        if clarification is None or _has_unsafe_format(clarification.question):
            failures.append(ValidationFailureCode.INVALID_CLARIFICATION)
            return
        if _facts(clarification.question) or _URL_OR_EMAIL_RE.search(clarification.question):
            failures.append(ValidationFailureCode.INVALID_CLARIFICATION)

        if any(answer_context[field] is not None for field in clarification.fields):
            failures.append(ValidationFailureCode.INVALID_CLARIFICATION)
        if clarification.options:
            allowed: set[str] = set()
            for field in clarification.fields:
                allowed.update(known_context_values.get(field, frozenset()))
            supplied = {_normalize_phrase(option) for option in clarification.options}
            if not allowed or not supplied.issubset(allowed):
                failures.append(ValidationFailureCode.INVALID_CLARIFICATION)

    @staticmethod
    def _index_evidence(
        evidence: Sequence[RetrievedEvidence],
        failures: list[ValidationFailureCode],
    ) -> dict[UUID, RetrievedEvidence]:
        index: dict[UUID, RetrievedEvidence] = {}
        for item in evidence:
            if not isinstance(item, RetrievedEvidence) or not _evidence_is_well_formed(item):
                failures.append(ValidationFailureCode.INVALID_EVIDENCE)
                continue
            try:
                fingerprint = _evidence_fingerprint(item)
                existing = index.get(item.evidence_id)
                existing_fingerprint = (
                    _evidence_fingerprint(existing) if existing is not None else None
                )
            except (TypeError, ValueError):
                failures.append(ValidationFailureCode.INVALID_EVIDENCE)
                continue
            if existing_fingerprint is not None and existing_fingerprint != fingerprint:
                failures.append(ValidationFailureCode.INVALID_EVIDENCE)
                continue
            index[item.evidence_id] = item
        return index

    @staticmethod
    def _citations(
        candidate: StructuredAnswer,
        evidence_index: Mapping[UUID, RetrievedEvidence],
        failures: list[ValidationFailureCode],
    ) -> tuple[EvidenceCitation, ...]:
        ordered_ids = tuple(
            dict.fromkeys(
                evidence_id
                for segment in candidate.segments
                for evidence_id in segment.evidence_ids
            )
        )
        if len(ordered_ids) > MAX_CITATIONS:
            failures.append(ValidationFailureCode.INVALID_CITATION)
            return ()

        citations: list[EvidenceCitation] = []
        for evidence_id in ordered_ids:
            item = evidence_index.get(evidence_id)
            if item is None:
                failures.append(ValidationFailureCode.UNKNOWN_EVIDENCE_ID)
                continue
            citation = citation_from_evidence(item)
            if citation is None:
                failures.append(ValidationFailureCode.INVALID_CITATION)
                continue
            citations.append(citation)
        return tuple(citations)

    @staticmethod
    def _rejection(failures: tuple[ValidationFailureCode, ...]) -> ValidationResult:
        answer = StructuredAnswer(
            outcome=AnswerOutcome.UNABLE,
            segments=(
                AnswerSegment(
                    kind=SegmentKind.LIMITATION,
                    text=SAFE_VALIDATION_LIMITATION,
                    evidence_ids=(),
                ),
            ),
            clarification=None,
            reason_code=ReasonCode.MISSING_EVIDENCE,
        )
        return ValidationResult(
            accepted=False,
            answer=answer,
            citations=(),
            failures=failures,
        )


def citation_from_evidence(evidence: RetrievedEvidence) -> EvidenceCitation | None:
    """Derive a public citation only from well-formed retrieved evidence metadata."""

    title = evidence.source_title.strip()
    parsed = urlsplit(evidence.canonical_url)
    if (
        not title
        or _CONTROL_RE.search(title) is not None
        or _has_unsafe_format(title)
        or parsed.scheme != "https"
        or not parsed.hostname
        or _CONTROL_RE.search(evidence.canonical_url) is not None
        or any(character.isspace() for character in evidence.canonical_url)
        or parsed.username is not None
        or parsed.password is not None
        or (evidence.page is not None and evidence.page < 1)
    ):
        return None

    fragment = parsed.fragment
    if evidence.anchor:
        anchor = evidence.anchor.strip()
        if not anchor or len(anchor) > 512:
            return None
        fragment = quote(anchor, safe="-._~")
    url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, fragment))
    section = " > ".join(part.strip() for part in evidence.heading_path if part.strip()) or None
    if section is None and evidence.anchor:
        section = evidence.anchor.strip()
    if section is not None and (
        _CONTROL_RE.search(section) is not None or _has_unsafe_format(section)
    ):
        return None
    return EvidenceCitation(
        id=str(evidence.evidence_id),
        evidence_id=evidence.evidence_id,
        source_title=title,
        url=url,
        section=section,
        page=evidence.page,
        version_id=evidence.version_id,
    )


def _scope_matches(evidence: RetrievedEvidence, scope: RetrievalScope) -> bool:
    applicable = evidence.applicability
    return (
        applicable.topic == scope.topic
        and evidence.topic_key == scope.topic
        and applicable.institution == scope.institution
        and applicable.campus == scope.campus
        and applicable.student_level == scope.student_level
        and applicable.program == scope.program
        and applicable.catalog_year == scope.catalog_year
        and applicable.term == scope.term
        and applicable.session == scope.session
        and applicable.year == scope.year
    )


def _answer_context(
    scope: RetrievalScope,
    explicit_context: Mapping[ContextField, str] | None,
) -> dict[ContextField, str | None]:
    context: dict[ContextField, str | None] = {
        ContextField.CAMPUS: scope.campus,
        ContextField.TERM: scope.term,
        ContextField.YEAR: scope.year,
        ContextField.SESSION: scope.session,
        ContextField.PROGRAM: scope.program,
        ContextField.STUDENT_LEVEL: scope.student_level,
        ContextField.CATALOG_YEAR: scope.catalog_year,
    }
    if explicit_context is None:
        return context
    for context_field, value in explicit_context.items():
        if not isinstance(context_field, ContextField) or not isinstance(value, str):
            raise TypeError("explicit context is invalid")
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("explicit context is invalid")
        existing = context[context_field]
        if existing is not None and existing != cleaned:
            raise ValueError("explicit context conflicts with retrieval scope")
        context[context_field] = cleaned
    return context


def _normalize_context_values(
    values: Mapping[ContextField, Collection[str]] | None,
) -> dict[ContextField, frozenset[str]]:
    if values is None:
        return {}
    normalized: dict[ContextField, frozenset[str]] = {}
    for field, options in values.items():
        if not isinstance(field, ContextField):
            raise TypeError("known context keys must be context fields")
        if any(not isinstance(option, str) for option in options):
            raise TypeError("known context values must be strings")
        cleaned = frozenset(_normalize_phrase(option) for option in options if option.strip())
        normalized[field] = cleaned
    return normalized


def _mentions_mismatched_scope(
    text: str,
    *,
    answer_context: Mapping[ContextField, str | None],
    known_context_values: Mapping[ContextField, frozenset[str]],
) -> bool:
    normalized_text = f" {_normalize_phrase(text)} "
    for field, options in known_context_values.items():
        expected_value = answer_context[field]
        normalized_expected = _normalize_phrase(expected_value) if expected_value else None
        for option in options:
            if f" {option} " in normalized_text and option != normalized_expected:
                return True
    return False


def _facts_share_evidence(
    claim_facts: frozenset[_Fact],
    evidence: Sequence[RetrievedEvidence],
) -> bool:
    if not claim_facts:
        return True
    if any(fact.kind in {"invalid_date", "invalid_time"} for fact in claim_facts):
        return False
    return any(
        _facts_supported_by_text(claim_facts, _complete_evidence_text(item)) for item in evidence
    )


def _table_relationships_match(
    claim: str,
    claim_facts: frozenset[_Fact],
    evidence: Sequence[RetrievedEvidence],
) -> bool:
    table_facts = frozenset(
        fact
        for fact in claim_facts
        if fact.kind in {"date", "percent", "currency", "time", "number"}
    )
    table_evidence = tuple(
        item for item in evidence if _has_keys(item.structured_content, _TABLE_KEYS)
    )
    if not table_evidence or len(table_facts) < 2:
        return True
    claim_tokens = _content_tokens(claim)
    for item in table_evidence:
        structured_text = json.dumps(item.structured_content, ensure_ascii=False, sort_keys=True)
        structured_claim_tokens = claim_tokens & _content_tokens(structured_text)
        for record in _structured_records(item.structured_content):
            if not _facts_supported_by_text(table_facts, record):
                continue
            if structured_claim_tokens.issubset(_content_tokens(record)):
                return True
    return False


def _prerequisites_match(
    claim: str,
    claim_facts: frozenset[_Fact],
    evidence: Sequence[RetrievedEvidence],
) -> bool:
    lowered = _normalize_phrase(claim)
    course_facts = frozenset(fact for fact in claim_facts if fact.kind in {"course", "grade"})
    relation_words = {
        word for word in ("and", "or") if re.search(rf"\b{word}\b", lowered, re.IGNORECASE)
    }
    relation_kind = next(
        (
            kind
            for kind in ("prerequisite", "corequisite", "equivalent")
            if kind in lowered or f"{kind[:-1]}ies" in lowered
        ),
        None,
    )
    is_prerequisite_claim = relation_kind is not None or (
        len(tuple(fact for fact in course_facts if fact.kind == "course")) >= 2
        and bool(relation_words)
    )
    if not is_prerequisite_claim:
        return True

    for item in evidence:
        records = (
            _structured_records(item.structured_content)
            if _has_keys(item.structured_content, _PREREQUISITE_KEYS)
            else (_complete_evidence_text(item),)
        )
        for record in records:
            normalized_record = _normalize_phrase(record)
            if relation_kind and relation_kind not in normalized_record:
                continue
            if any(
                not re.search(rf"\b{operator}\b", normalized_record) for operator in relation_words
            ):
                continue
            if _facts_supported_by_text(course_facts, record):
                return True
    return False


def _modal_meaning_matches(claim: str, evidence: Sequence[RetrievedEvidence]) -> bool:
    claim_categories = _modal_categories(claim)
    if not claim_categories:
        return True
    return any(
        claim_categories.issubset(_modal_categories(_complete_evidence_text(item)))
        for item in evidence
    )


def _modal_categories(text: str) -> frozenset[str]:
    normalized = _normalize_phrase(text)
    categories: set[str] = set()
    if re.search(r"\b(?:must\s+not|cannot|can't|prohibited|forbidden|not\s+allowed)\b", normalized):
        categories.add("prohibition")
    if re.search(r"\b(?:must|required|requirement|shall|need(?:s)?\s+to)\b", normalized):
        categories.add("obligation")
    if re.search(r"\b(?:may|allowed|permitted|can)\b", normalized):
        categories.add("permission")
    if re.search(r"\b(?:not|no|never|cannot|can't|doesn't|isn't|aren't)\b", normalized):
        categories.add("negation")
    return frozenset(categories)


def _past_dates_are_labeled(text: str, facts: frozenset[_Fact], today: date) -> bool:
    dated_facts = tuple(
        fact for fact in facts if fact.kind == "date" and not fact.value.startswith("*")
    )
    for fact in dated_facts:
        try:
            stated_date = date.fromisoformat(fact.value)
        except ValueError:
            return False
        if stated_date < today and not _PAST_MARKER_RE.search(text):
            return False
    return True


def _claim_support_text(text: str, facts: frozenset[_Fact], today: date) -> str:
    """Remove a derived past-status label only when the sentence contains a past date."""

    stated_dates: list[date] = []
    for fact in facts:
        if fact.kind != "date" or fact.value.startswith("*"):
            continue
        try:
            stated_dates.append(date.fromisoformat(fact.value))
        except ValueError:
            continue
    if stated_dates and all(stated_date < today for stated_date in stated_dates):
        return _PAST_MARKER_RE.sub(" ", text)
    return text


def _is_safe_limitation(text: str) -> bool:
    return bool(
        _SAFE_LIMITATION_RE.search(text)
        and not _facts(text)
        and not _URL_OR_EMAIL_RE.search(text)
        and not _DIRECTIVE_RE.search(text)
        and not _has_unsafe_format(text)
    )


def _has_unsafe_format(text: str) -> bool:
    return bool(_HTML_RE.search(text) or _MARKDOWN_LINK_RE.search(text) or "```" in text)


def _sentences(text: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in _SENTENCE_RE.split(text) if part.strip())


def _facts(text: str) -> frozenset[_Fact]:
    facts: set[_Fact] = set()
    occupied: list[tuple[int, int]] = []

    def add(match: re.Match[str], fact: _Fact | None) -> None:
        if fact is None:
            return
        facts.add(fact)
        occupied.append(match.span())

    for match in _ISO_DATE_RE.finditer(text):
        add(match, _date_fact(match.group("year"), match.group("month"), match.group("day")))
    for match in _NUMERIC_DATE_RE.finditer(text):
        if _overlaps(match.span(), occupied):
            continue
        add(match, _date_fact(match.group("year"), match.group("month"), match.group("day")))
    for match in _NAMED_DATE_RE.finditer(text):
        if _overlaps(match.span(), occupied):
            continue
        month = str(_MONTHS[match.group("month").rstrip(".").casefold()])
        add(match, _date_fact(match.group("year"), month, match.group("day")))
    for match in _COURSE_RE.finditer(text):
        if match.group("prefix").upper() in _COURSE_PREFIX_EXCLUSIONS:
            continue
        add(
            match,
            _Fact("course", f"{match.group('prefix').upper()}{match.group('number').upper()}"),
        )
    for regex, kind in (
        (_PERCENT_RE, "percent"),
        (_CURRENCY_RE, "currency"),
        (_TIME_RE, "time"),
        (_GRADE_RE, "grade"),
    ):
        for match in regex.finditer(text):
            if _overlaps(match.span(), occupied):
                continue
            if kind == "time":
                value = _normalized_time(match)
            elif kind == "grade":
                value = match.group("grade").upper()
            else:
                value = _normalized_number(match.group("number"))
            fact_kind = "invalid_time" if kind == "time" and value == "invalid" else kind
            add(match, _Fact(fact_kind, value))
    for match in _NUMBER_RE.finditer(text):
        if not _overlaps(match.span(), occupied):
            add(match, _Fact("number", _normalized_number(match.group())))
    return frozenset(facts)


def _date_fact(year: str | None, month: str, day: str) -> _Fact:
    normalized_year = "*" if year is None else year
    if len(normalized_year) == 2:
        normalized_year = f"20{normalized_year}"
    try:
        normalized_month = int(month)
        normalized_day = int(day)
        validation_year = 2000 if normalized_year == "*" else int(normalized_year)
        date(validation_year, normalized_month, normalized_day)
    except ValueError:
        return _Fact("invalid_date", f"{normalized_year}-{month}-{day}")
    return _Fact("date", f"{normalized_year}-{normalized_month:02d}-{normalized_day:02d}")


def _facts_supported_by_text(facts: Collection[_Fact], text: str) -> bool:
    if any(fact.kind in {"invalid_date", "invalid_time"} for fact in facts):
        return False
    supported = _facts(text)
    for fact in facts:
        if fact.kind == "date" and fact.value.startswith("*"):
            if not any(
                candidate.kind == "date" and candidate.value.endswith(fact.value[1:])
                for candidate in supported
            ):
                return False
        elif fact not in supported:
            return False
    return True


def _normalized_time(match: re.Match[str]) -> str:
    hour = int(match.group("hour"))
    minute = int(match.group("minute") or "0")
    period = match.group("period").replace(".", "").casefold()
    if hour not in range(1, 13) or minute not in range(60):
        return "invalid"
    if period == "pm" and hour != 12:
        hour += 12
    if period == "am" and hour == 12:
        hour = 0
    return f"{hour:02d}:{minute:02d}"


def _normalized_number(value: str) -> str:
    cleaned = value.replace(",", "").strip()
    if "." in cleaned:
        cleaned = cleaned.rstrip("0").rstrip(".")
    return cleaned


def _structured_records(value: object) -> tuple[str, ...]:
    records: list[str] = []

    def visit(node: object, inherited: tuple[str, ...] = ()) -> None:
        if isinstance(node, Mapping):
            headers = _scalar_sequence(node.get("headers")) or _scalar_sequence(node.get("columns"))
            rows = node.get("rows")
            if headers and isinstance(rows, (list, tuple)):
                for row in rows:
                    cells = _scalar_sequence(row)
                    if cells and len(cells) == len(headers):
                        records.append(
                            " ".join(
                                (
                                    *inherited,
                                    *(
                                        f"{header} {cell}"
                                        for header, cell in zip(headers, cells, strict=True)
                                    ),
                                )
                            )
                        )
                    else:
                        visit(row, (*inherited, *headers))
                for key, item in node.items():
                    if key not in {"headers", "columns", "rows"}:
                        visit(item, inherited)
                return
            scalar_parts = tuple(
                f"{key} {item}"
                for key, item in node.items()
                if isinstance(item, (str, int, float, bool)) or item is None
            )
            children = tuple(
                item for item in node.values() if isinstance(item, (Mapping, list, tuple))
            )
            if not children:
                records.append(" ".join((*inherited, *scalar_parts)))
            else:
                for child in children:
                    visit(child, (*inherited, *scalar_parts))
        elif isinstance(node, (list, tuple)):
            if all(not isinstance(item, (Mapping, list, tuple)) for item in node):
                records.append(" ".join((*inherited, *(str(item) for item in node))))
            else:
                for item in node:
                    visit(item, inherited)
        else:
            records.append(" ".join((*inherited, str(node))))

    visit(value)
    return tuple(record for record in records if record.strip())


def _scalar_sequence(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or any(
        isinstance(item, (Mapping, list, tuple)) for item in value
    ):
        return ()
    return tuple(str(item) for item in value)


def _has_keys(value: object, keys: frozenset[str]) -> bool:
    if isinstance(value, Mapping):
        if any(_normalize_phrase(str(key)).replace(" ", "_") in keys for key in value):
            return True
        return any(_has_keys(item, keys) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_has_keys(item, keys) for item in value)
    return False


def _complete_evidence_text(evidence: RetrievedEvidence) -> str:
    structured = json.dumps(
        evidence.structured_content,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    scope = json.dumps(
        evidence.scope,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return " ".join((*evidence.heading_path, evidence.text, structured, scope))


def _evidence_support_units(evidence: RetrievedEvidence) -> tuple[str, ...]:
    applicable = evidence.applicability
    context = " ".join(
        value
        for value in (
            applicable.topic,
            applicable.institution,
            applicable.campus,
            applicable.student_level,
            applicable.program,
            applicable.catalog_year,
            applicable.term,
            applicable.session,
            applicable.year,
        )
        if value is not None
    )
    prefix = " ".join((*evidence.heading_path, context))
    text_units = tuple(f"{prefix} {sentence}" for sentence in _sentences(evidence.text))
    structured_units = tuple(
        f"{prefix} {record}" for record in _structured_records(evidence.structured_content)
    )
    scope_unit = f"{prefix} {json.dumps(evidence.scope, ensure_ascii=False, sort_keys=True)}"
    return (*text_units, *structured_units, scope_unit)


def _evidence_is_well_formed(evidence: RetrievedEvidence) -> bool:
    applicable = evidence.applicability
    optional_scope = (
        applicable.campus,
        applicable.student_level,
        applicable.program,
        applicable.catalog_year,
        applicable.term,
        applicable.session,
        applicable.year,
    )
    return bool(
        isinstance(evidence.evidence_id, UUID)
        and isinstance(evidence.version_id, UUID)
        and isinstance(evidence.source_id, UUID)
        and isinstance(evidence.source_title, str)
        and evidence.source_title.strip()
        and isinstance(evidence.canonical_url, str)
        and isinstance(evidence.ordinal, int)
        and evidence.ordinal >= 0
        and all(isinstance(part, str) for part in evidence.heading_path)
        and (evidence.page is None or (isinstance(evidence.page, int) and evidence.page >= 1))
        and (evidence.anchor is None or isinstance(evidence.anchor, str))
        and isinstance(evidence.text, str)
        and evidence.text.strip()
        and isinstance(evidence.structured_content, (dict, list))
        and isinstance(evidence.scope, dict)
        and isinstance(evidence.topic_key, str)
        and evidence.topic_key.strip()
        and isinstance(applicable.topic, str)
        and applicable.topic.strip()
        and isinstance(applicable.institution, str)
        and applicable.institution.strip()
        and all(
            value is None or (isinstance(value, str) and value.strip()) for value in optional_scope
        )
        and isinstance(evidence.model_revision, str)
        and evidence.model_revision.strip()
    )


def _evidence_fingerprint(evidence: RetrievedEvidence) -> tuple[object, ...]:
    applicability = evidence.applicability
    return (
        evidence.version_id,
        evidence.source_id,
        evidence.source_title,
        evidence.canonical_url,
        evidence.ordinal,
        evidence.heading_path,
        evidence.page,
        evidence.anchor,
        evidence.text,
        json.dumps(evidence.structured_content, sort_keys=True, separators=(",", ":")),
        evidence.topic_key,
        json.dumps(evidence.scope, sort_keys=True, separators=(",", ":")),
        applicability.topic,
        applicability.institution,
        applicability.campus,
        applicability.student_level,
        applicability.program,
        applicability.catalog_year,
        applicability.term,
        applicability.session,
        applicability.year,
        evidence.model_revision,
    )


def _content_tokens(text: str) -> set[str]:
    tokens: set[str] = set()
    for match in _WORD_RE.finditer(_normalize_phrase(text)):
        token = _word_family(match.group())
        if len(token) > 1 and token not in _STOPWORDS:
            tokens.add(token)
    return tokens


def _word_family(word: str) -> str:
    aliases = {
        "application": "apply",
        "applications": "apply",
        "applied": "apply",
        "payment": "pay",
        "payments": "pay",
        "percent": "percentage",
        "registration": "register",
        "registered": "register",
        "registering": "register",
        "requirements": "required",
        "requirement": "required",
        "requires": "required",
        "must": "obligation",
        "shall": "obligation",
        "required": "obligation",
        "needs": "obligation",
        "need": "obligation",
        "may": "permission",
        "can": "permission",
        "allowed": "permission",
        "permitted": "permission",
    }
    return aliases.get(word, word)


def _normalize_phrase(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(_WORD_RE.findall(normalized))


def _overlaps(span: tuple[int, int], occupied: Sequence[tuple[int, int]]) -> bool:
    return any(span[0] < end and start < span[1] for start, end in occupied)


def _validated_time(value: datetime | None) -> datetime:
    checked = datetime.now(UTC) if value is None else value
    if checked.tzinfo is None or checked.utcoffset() is None:
        raise ValueError("observed time must be timezone-aware")
    return checked.astimezone(UTC)
