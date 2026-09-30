from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any


_CAMPUS_ALIASES = {
    "hammond": "hammond",
    "hammond campus": "hammond",
    "westville": "westville",
    "westville campus": "westville",
    "calumet": "calumet",
    "calumet campus": "calumet",
    "lawrence": "lawrence",
    "lawrence campus": "lawrence",
    "pnw": "pnw",
    "purdue university northwest": "pnw",
    "both campuses": "both",
    "either campus": "both",
}

_TERM_ALIASES = {
    "fall": "fall",
    "spring": "spring",
    "summer": "summer",
    "winter": "winter",
    "current": "current",
    "this term": "current",
    "next term": "next",
    "upcoming term": "next",
    "academic year": "academic year",
}

_LEVEL_ALIASES = {
    "undergraduate": "undergraduate",
    "undergrad": "undergraduate",
    "freshman": "freshman",
    "first year": "freshman",
    "sophomore": "sophomore",
    "junior": "junior",
    "senior": "senior",
    "graduate": "graduate",
    "grad": "graduate",
    "graduate student": "graduate",
    "masters": "graduate",
    "master\'s": "graduate",
    "master": "graduate",
    "doctoral": "graduate",
    "phd": "graduate",
}


@dataclass(slots=True, frozen=True)
class QuestionScope:
    campus: str | None = None
    academic_term: str | None = None
    program: str | None = None
    course: str | None = None
    student_level: str | None = None
    requires_personal_record: bool = False
    topic: str | None = None
    missing_context: list[str] = field(default_factory=list)

    @property
    def scope(self) -> dict[str, str | bool | None]:
        return {
            "campus": self.campus,
            "academic_term": self.academic_term,
            "program": self.program,
            "course": self.course,
            "student_level": self.student_level,
            "requires_personal_record": self.requires_personal_record,
            "topic": self.topic,
        }


@dataclass(slots=True, frozen=True)
class QuestionIntent:
    intent: str
    campus: str | None = None
    academic_term: str | None = None
    program: str | None = None
    course: str | None = None
    student_level: str | None = None
    requires_personal_record: bool = False
    topic: str | None = None
    missing_context: list[str] = field(default_factory=list)

    def as_scope(self) -> QuestionScope:
        return QuestionScope(
            campus=self.campus,
            academic_term=self.academic_term,
            program=self.program,
            course=self.course,
            student_level=self.student_level,
            requires_personal_record=self.requires_personal_record,
            topic=self.topic,
            missing_context=self.missing_context,
        )

    @property
    def scope(self) -> dict[str, str | bool | None]:
        return self.as_scope().scope

    @property
    def requires_follow_up(self) -> bool:
        return bool(self.missing_context)

    @property
    def campus_hint(self) -> str | None:
        return self.campus

    @property
    def term_hint(self) -> str | None:
        return self.academic_term


def _normalize_text(value: str | None) -> str:
    if value is None:
        return ""
    text = value.strip().lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9\s/\-]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_year(text: str) -> str | None:
    match = re.search(r"\b(?:20|19)\d{2}\b", text)
    if match:
        return match.group(0)
    return None


def _extract_campus(text: str) -> str | None:
    if not text:
        return None
    normalized = _normalize_text(text)
    for key, value in _CAMPUS_ALIASES.items():
        if key in normalized:
            return value

    if "hammond" in normalized:
        return "hammond"
    if "westville" in normalized:
        return "westville"
    if "calumet" in normalized:
        return "calumet"
    if "lawrence" in normalized:
        return "lawrence"
    return None


def _extract_term(text: str) -> str | None:
    normalized = _normalize_text(text)
    for token in ("spring", "fall", "summer", "winter"):
        pattern = rf"\b{token}\b"
        if re.search(pattern, normalized):
            year = _extract_year(normalized)
            if year:
                return f"{token} {year}"
            return token

    if "this term" in normalized or "current term" in normalized or "current semester" in normalized:
        return "current"
    if "next term" in normalized or "upcoming term" in normalized:
        return "next"
    if "academic year" in normalized:
        return "academic year"
    return None


def _extract_program(text: str) -> str | None:
    normalized = _normalize_text(text)
    patterns = [
        r"\b(?:program|degree|major|minor|certificate|track|option)\s+(?:in|for|on)\s+([a-z0-9][a-z0-9 &/\-]+?)(?:\?|\.|,|;|$)",
        r"\b(?:programs|degrees|majors|minors|certificates|tracks|options)\b.*?\b(?:in|for|on)\s+([a-z0-9][a-z0-9 &/\-]+?)(?:\?|\.|,|;|$)",
        r"\b(?:in|for)\s+([a-z0-9][a-z0-9 &/\-]+?)\s+(?:program|degree|major|minor|certificate|track|option|programs|degrees|majors|minors|certificates|tracks|options)\b",
    ]

    for pattern in patterns:
        match = re.search(pattern, normalized)
        if match:
            value = match.group(1).strip()
            if value:
                return value

    return None


def _extract_course(text: str) -> str | None:
    normalized = _normalize_text(text)
    match = re.search(r"\b([a-z]{2,6})\s*-?\s*(\d{3,5})[a-z]?\b", normalized)
    if match:
        code = f"{match.group(1)} {match.group(2)}"
        if any(token in normalized for token in ("course", "class", "prereq", "prerequisite", "enroll")):
            return code
    if re.search(r"\b(?:course|class)\s+(?:number|code)?\s*[a-z]{2,6}\s*-?\s*\d{3,5}\b", normalized):
        match = re.search(r"\b([a-z]{2,6})\s*-?\s*(\d{3,5})[a-z]?\b", normalized)
        if match:
            return f"{match.group(1)} {match.group(2)}"
    return None


def _extract_student_level(text: str) -> str | None:
    normalized = _normalize_text(text)
    for key, value in _LEVEL_ALIASES.items():
        if key in normalized:
            return value
    return None


def _requires_personal_record(text: str) -> bool:
    normalized = _normalize_text(text)
    personal_markers = {"my", "mine", "i", "me", "their", "our", "personal", "student record"}
    record_markers = {
        "record",
        "transcript",
        "gpa",
        "grade",
        "financial aid",
        "award",
        "aid",
        "registration",
        "degree audit",
        "academic standing",
        "holds",
        "account",
        "advisement",
        "disciplinary",
        "conduct",
    }

    if "personal record" in normalized or "student record" in normalized:
        return True

    if any(marker in normalized for marker in personal_markers) and any(
        marker in normalized for marker in record_markers
    ):
        return True

    if re.search(r"\b(?:my|i|their|our)\b.*\b(?:grade|gpa|aid|award|transcript|record|account|status)\b", normalized):
        return True

    return False


def _classify_intent(text: str) -> str:
    normalized = _normalize_text(text)
    if _requires_personal_record(normalized):
        return "personal_record"
    if _extract_course(normalized):
        return "course"
    if _extract_program(normalized):
        return "program"
    if _extract_student_level(normalized):
        return "student_level"
    if "deadline" in normalized or "due date" in normalized or "last day" in normalized:
        return "deadline"
    if "prerequisite" in normalized or "requirement" in normalized or "eligibility" in normalized:
        return "requirement"
    if "how do i" in normalized or "how can i" in normalized or "steps" in normalized or "procedure" in normalized:
        return "procedure"
    if "parking" in normalized or "policy" in normalized or "rules" in normalized:
        return "general"
    return "general"


@dataclass(slots=True, frozen=True)
class EvidenceAssessment:
    can_answer: bool
    has_sufficient_evidence: bool
    has_stale_source: bool
    has_conflicting_sources: bool
    source_count: int = 0
    fresh_source_count: int = 0
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "can_answer": self.can_answer,
            "has_sufficient_evidence": self.has_sufficient_evidence,
            "has_stale_source": self.has_stale_source,
            "has_conflicting_sources": self.has_conflicting_sources,
            "source_count": self.source_count,
            "fresh_source_count": self.fresh_source_count,
            "reasons": list(self.reasons),
        }


class PolicyService:
    """Classify student questions and guard answer generation against stale or conflicting evidence."""

    @staticmethod
    def _read_field(source: Any, *names: str) -> Any:
        if source is None:
            return None
        if isinstance(source, Mapping):
            for name in names:
                if name in source:
                    return source[name]
            return None
        for name in names:
            if hasattr(source, name):
                return getattr(source, name)
        return None

    @staticmethod
    def _coerce_date(value: Any) -> date | None:
        if value is None:
            return None
        if isinstance(value, date) and not isinstance(value, datetime):
            return value
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
                try:
                    return datetime.strptime(text, fmt).date()
                except ValueError:
                    continue
        return None

    @staticmethod
    def _source_is_stale(source: Any, *, freshness_window_days: int = 365) -> bool:
        status = PolicyService._read_field(source, "status", "source_status", "review_status")
        normalized_status = str(status).lower() if status is not None else ""
        if normalized_status in {"archived", "disputed", "conflict", "conflicted", "needs_review", "unreviewed"}:
            return True
        if normalized_status == "active":
            age_days = None
            updated = PolicyService._coerce_date(
                PolicyService._read_field(source, "last_updated", "updated_at", "review_date")
            )
            if updated is not None:
                age_days = (date.today() - updated).days
            if age_days is not None and age_days > freshness_window_days:
                return True
            if PolicyService._read_field(source, "is_fresh") is False:
                return True
            return False

        updated = PolicyService._coerce_date(
            PolicyService._read_field(source, "last_updated", "updated_at", "review_date")
        )
        if updated is not None and (date.today() - updated).days > freshness_window_days:
            return True
        if PolicyService._read_field(source, "is_fresh") is False:
            return True
        return False

    @staticmethod
    def _source_has_explicit_conflict(source: Any) -> bool:
        for key in (
            "has_conflict",
            "material_conflict",
            "conflict",
            "conflicting_source",
            "conflicting_sources",
            "is_conflicting",
        ):
            value = PolicyService._read_field(source, key)
            if isinstance(value, bool):
                if value:
                    return True
            elif value is not None:
                if str(value).lower() in {"true", "yes", "conflict", "conflicted"}:
                    return True
        status = PolicyService._read_field(source, "status", "source_status", "review_status")
        normalized = str(status).lower() if status is not None else ""
        if normalized in {"conflict", "conflicted", "disputed", "needs_review", "unreviewed"}:
            return True
        return False

    @staticmethod
    def _content_for(source: Any) -> str:
        for key in ("content", "text", "body", "snippet", "quote", "summary"):
            value = PolicyService._read_field(source, key)
            if value is not None:
                text = str(value)
                if text.strip():
                    return text
        if isinstance(source, Mapping):
            return " ".join(str(v) for v in source.values() if isinstance(v, str))
        return str(source)

    @staticmethod
    def _detect_conflicting_sources(evidence: Sequence[Any] | Any | None) -> bool:
        if evidence is None:
            return False
        if not isinstance(evidence, Sequence) or isinstance(evidence, (str, bytes, bytearray)):
            evidence = [evidence]
        sources = [entry for entry in evidence if entry is not None]
        if not sources:
            return False
        if any(PolicyService._source_has_explicit_conflict(source) for source in sources):
            return True
        if len(sources) < 2:
            return False

        texts = []
        for source in sources:
            text = PolicyService._content_for(source)
            if text.strip():
                texts.append(_normalize_text(text))

        if len(texts) < 2:
            return False

        for idx, first in enumerate(texts):
            first_tokens = set(first.split())
            for second in texts[idx + 1 :]:
                second_tokens = set(second.split())
                if not first_tokens or not second_tokens:
                    continue
                overlap = first_tokens & second_tokens
                if not overlap:
                    continue
                first_has_negation = any(token in first for token in ("not", "never", "no", "cannot", "must not", "do not"))
                second_has_negation = any(token in second for token in ("not", "never", "no", "cannot", "must not", "do not"))
                if first_has_negation != second_has_negation and overlap:
                    return True
                if first != second and len(overlap) > 0 and any(
                    phrase in first for phrase in ("required", "must", "deadline", "due", "eligible")
                ) and any(
                    phrase in second for phrase in ("not required", "not eligible", "not due", "optional")
                ):
                    return True
        return False

    @staticmethod
    def assess_evidence(
        evidence: Sequence[Any] | Any | None,
        *,
        freshness_window_days: int = 365,
    ) -> EvidenceAssessment:
        if evidence is None:
            sources: list[Any] = []
        elif isinstance(evidence, Sequence) and not isinstance(evidence, (str, bytes, bytearray)):
            sources = list(evidence)
        else:
            sources = [evidence]

        reasons: list[str] = []
        stale_sources = [source for source in sources if PolicyService._source_is_stale(source, freshness_window_days=freshness_window_days)]
        conflicting_sources = PolicyService._detect_conflicting_sources(sources)
        fresh_sources = [
            source
            for source in sources
            if not PolicyService._source_is_stale(source, freshness_window_days=freshness_window_days)
        ]

        if not sources:
            reasons.append("No source evidence is available for an answer.")
        elif stale_sources:
            reasons.append("One or more retrieved sources are stale or archived.")
        if conflicting_sources:
            reasons.append("The evidence contains material conflicts and cannot be used without review.")
        if not sources:
            return EvidenceAssessment(
                can_answer=False,
                has_sufficient_evidence=False,
                has_stale_source=False,
                has_conflicting_sources=False,
                source_count=0,
                fresh_source_count=0,
                reasons=tuple(reasons),
            )

        if stale_sources:
            fresh_sources = [
                source
                for source in fresh_sources
                if not PolicyService._source_has_explicit_conflict(source)
            ]

        has_sufficient_evidence = bool(fresh_sources) and not stale_sources and not conflicting_sources
        can_answer = has_sufficient_evidence

        if can_answer:
            reasons.append("Fresh, consistent evidence is available to answer the question.")
        elif not stale_sources and not conflicting_sources and not fresh_sources:
            reasons.append("No fresh evidence passed the source-quality checks.")

        return EvidenceAssessment(
            can_answer=can_answer,
            has_sufficient_evidence=has_sufficient_evidence,
            has_stale_source=bool(stale_sources),
            has_conflicting_sources=conflicting_sources,
            source_count=len(sources),
            fresh_source_count=len(fresh_sources),
            reasons=tuple(reasons),
        )

    @staticmethod
    def evaluate_evidence(
        evidence: Sequence[Any] | Any | None,
        *,
        freshness_window_days: int = 365,
    ) -> EvidenceAssessment:
        return PolicyService.assess_evidence(evidence, freshness_window_days=freshness_window_days)

    @staticmethod
    def evaluate_answer_readiness(
        evidence: Sequence[Any] | Any | None,
        *,
        freshness_window_days: int = 365,
    ) -> EvidenceAssessment:
        return PolicyService.assess_evidence(evidence, freshness_window_days=freshness_window_days)

    @staticmethod
    def check_evidence_sufficiency(
        evidence: Sequence[Any] | Any | None,
        *,
        freshness_window_days: int = 365,
    ) -> bool:
        return PolicyService.assess_evidence(
            evidence,
            freshness_window_days=freshness_window_days,
        ).has_sufficient_evidence

    @staticmethod
    def detect_stale_sources(
        evidence: Sequence[Any] | Any | None,
        *,
        freshness_window_days: int = 365,
    ) -> bool:
        return PolicyService.assess_evidence(
            evidence,
            freshness_window_days=freshness_window_days,
        ).has_stale_source

    @staticmethod
    def detect_conflicting_sources(
        evidence: Sequence[Any] | Any | None,
        *,
        freshness_window_days: int = 365,
    ) -> bool:
        return PolicyService.assess_evidence(
            evidence,
            freshness_window_days=freshness_window_days,
        ).has_conflicting_sources

    @staticmethod
    def evidence_sufficiency(
        evidence: Sequence[Any] | Any | None,
        *,
        freshness_window_days: int = 365,
    ) -> bool:
        return PolicyService.check_evidence_sufficiency(
            evidence,
            freshness_window_days=freshness_window_days,
        )

    @staticmethod
    def evaluate_source_quality(
        evidence: Sequence[Any] | Any | None,
        *,
        freshness_window_days: int = 365,
    ) -> EvidenceAssessment:
        return PolicyService.assess_evidence(
            evidence,
            freshness_window_days=freshness_window_days,
        )

    @staticmethod
    def should_generate_answer(
        evidence: Sequence[Any] | Any | None,
        *,
        freshness_window_days: int = 365,
    ) -> bool:
        return PolicyService.assess_evidence(
            evidence,
            freshness_window_days=freshness_window_days,
        ).can_answer

    @staticmethod
    def answer_ready(
        evidence: Sequence[Any] | Any | None,
        *,
        freshness_window_days: int = 365,
    ) -> bool:
        return PolicyService.should_generate_answer(
            evidence,
            freshness_window_days=freshness_window_days,
        )


    @staticmethod
    def extract_intent_and_scope(
        question: str | None,
        *,
        session_context: Mapping[str, str | bool | None] | None = None,
    ) -> QuestionIntent:
        if question is None:
            question = ""

        normalized = _normalize_text(question)
        campus = _extract_campus(normalized)
        academic_term = _extract_term(normalized)
        program = _extract_program(normalized)
        course = _extract_course(normalized)
        student_level = _extract_student_level(normalized)
        personal_record = _requires_personal_record(normalized)
        topic = None

        if session_context:
            session_map = {str(key).lower(): value for key, value in session_context.items()}
            if campus is None:
                campus = (
                    session_map.get("campus")
                    or session_map.get("campus_context")
                    or session_map.get("campus_hint")
                )
            if academic_term is None:
                academic_term = (
                    session_map.get("academic_term")
                    or session_map.get("term")
                    or session_map.get("term_context")
                    or session_map.get("term_hint")
                )
            if program is None:
                program = session_map.get("program") or session_map.get("program_scope")
            if course is None:
                course = session_map.get("course") or session_map.get("course_scope")
            if student_level is None:
                student_level = session_map.get("student_level")
            if not personal_record:
                personal_record = bool(session_map.get("requires_personal_record"))

        campus = str(campus).strip() if campus else None
        academic_term = str(academic_term).strip() if academic_term else None
        program = str(program).strip() if program else None
        course = str(course).strip() if course else None
        student_level = str(student_level).strip() if student_level else None

        missing_context = []
        if campus is None and ("campus" in normalized or "on campus" in normalized):
            missing_context.append("campus")
        if academic_term is None and (
            "fall" in normalized or "spring" in normalized or "summer" in normalized or "winter" in normalized
        ):
            missing_context.append("academic_term")

        intent = _classify_intent(normalized)
        if campus is None and academic_term is not None and intent == "general":
            intent = "scope"

        return QuestionIntent(
            intent=intent,
            campus=campus,
            academic_term=academic_term,
            program=program,
            course=course,
            student_level=student_level,
            requires_personal_record=personal_record,
            topic=topic,
            missing_context=missing_context,
        )

    @staticmethod
    def classify_intent(question: str | None) -> str:
        if question is None:
            return "general"
        return PolicyService.extract_intent_and_scope(question).intent

    @staticmethod
    def extract_intent(question: str | None) -> str:
        return PolicyService.classify_intent(question)

    @staticmethod
    def extract_scope(
        question: str | None,
        *,
        session_context: Mapping[str, str | bool | None] | None = None,
    ) -> dict[str, str | bool | None]:
        return PolicyService.extract_intent_and_scope(
            question,
            session_context=session_context,
        ).scope

    @staticmethod
    def get_scope(
        question: str | None,
        *,
        session_context: Mapping[str, str | bool | None] | None = None,
    ) -> dict[str, str | bool | None]:
        return PolicyService.extract_scope(question, session_context=session_context)

    @staticmethod
    def is_personal_record_request(question: str | None) -> bool:
        if question is None:
            return False
        return PolicyService.extract_intent_and_scope(question).requires_personal_record

    @staticmethod
    def detect_personal_record_request(question: str | None) -> bool:
        return PolicyService.is_personal_record_request(question)


__all__ = [
    "EvidenceAssessment",
    "QuestionIntent",
    "QuestionScope",
    "PolicyService",
    "_extract_campus",
    "_extract_term",
    "_extract_program",
    "_extract_course",
    "_extract_student_level",
    "_requires_personal_record",
]
