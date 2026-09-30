from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class EscalationDestination:
    name: str
    category: str
    url: str
    email: str | None = None
    phone: str | None = None
    description: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "category": self.category,
            "url": self.url,
            "email": self.email,
            "phone": self.phone,
            "description": self.description,
        }


@dataclass(frozen=True, slots=True)
class EscalationResult:
    answer_type: str
    message: str
    destination: EscalationDestination | None = None
    escalation_target: str | None = None

    @property
    def text(self) -> str:
        return self.message

    @property
    def target(self) -> str | None:
        return self.escalation_target

    def to_dict(self) -> dict[str, Any]:
        return {
            "answer_type": self.answer_type,
            "message": self.message,
            "destination": self.destination.to_dict() if self.destination else None,
            "escalation_target": self.escalation_target,
        }


_DEFAULT_DESTINATIONS: tuple[EscalationDestination, ...] = (
    EscalationDestination(
        name="PNW Registrar",
        category="registrar",
        url="https://www.pnw.edu/registrar/",
        description="Registration, records, deadlines, and academic calendar questions.",
    ),
    EscalationDestination(
        name="Office of Financial Aid",
        category="financial aid",
        url="https://www.pnw.edu/financial-aid/",
        description="Award, aid, billing, eligibility, and student-account questions.",
    ),
    EscalationDestination(
        name="Academic Advising",
        category="academic",
        url="https://www.pnw.edu/student-services/",
        description="Program, degree, prerequisite, and advising questions.",
    ),
    EscalationDestination(
        name="Dean of Students",
        category="dean of students",
        url="https://www.pnw.edu/dean-of-students/",
        description="Conduct, integrity, and student support questions.",
    ),
    EscalationDestination(
        name="Accessibility Services",
        category="accessibility",
        url="https://www.pnw.edu/accessibility/",
        description="Disability accommodations and accessibility support.",
    ),
    EscalationDestination(
        name="PNW Student Services",
        category="student services",
        url="https://www.pnw.edu/student-services/",
        description="General campus support and next-step referrals.",
    ),
)


def _normalize_text(value: str | None) -> str:
    if value is None:
        return ""
    text = value.strip().lower()
    text = re.sub(r"&", " and ", text)
    text = re.sub(r"[^a-z0-9\s/\-]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _match_category(text: str, category: str) -> bool:
    normalized = _normalize_text(text)
    return category.lower() in normalized or any(token in normalized for token in category.lower().split())


def _resolve_category(
    *,
    question: str | None = None,
    issue: str | None = None,
    reason: str | None = None,
    category: str | None = None,
    campus: str | None = None,
) -> str | None:
    if category:
        normalized = _normalize_text(category)
        if normalized:
            return normalized

    combined = " ".join(part for part in (question, issue, reason, campus) if part)
    text = _normalize_text(combined)
    if not text:
        return None

    if any(token in text for token in ("financial aid", "aid award", "scholarship", "loan", "grant")):
        return "financial aid"
    if any(token in text for token in ("registration", "register", "deadline", "calendar", "drop", "withdraw", "grade", "transcript", "gpa", "degree audit", "graduation")):
        return "registrar"
    if any(token in text for token in ("major", "degree", "program", "prerequisite", "advising", "plan of study", "catalog")):
        return "academic"
    if any(token in text for token in ("conduct", "disciplinary", "integrity", "complaint", "code of conduct")):
        return "dean of students"
    if any(token in text for token in ("accommodation", "accessibility", "disability", "support services")):
        return "accessibility"
    if any(token in text for token in ("parking", "campus services", "student services", "housing")):
        return "student services"
    return None


def _destination_for_category(category: str | None) -> EscalationDestination | None:
    if category is None:
        return _DEFAULT_DESTINATIONS[-1]

    normalized = _normalize_text(category)
    for destination in _DEFAULT_DESTINATIONS:
        if _normalize_text(destination.category) == normalized:
            return destination
        if destination.category in ("academic", "student services") and normalized in {"academic advising", "advising", "student service", "student services"}:
            return destination
    return _DEFAULT_DESTINATIONS[-1]


def _safe_limit_message(destination: EscalationDestination | None, *, reason: str | None = None) -> str:
    if destination is None:
        return (
            "I cannot provide a reliable answer for this request because the available information is insufficient or conflicted. "
            "Please contact the appropriate PNW office or advisor for an official answer."
        )

    if reason and "conflict" in _normalize_text(reason):
        return (
            f"I cannot provide a reliable answer for this request because the available information is conflicting or unclear. "
            f"Please contact {destination.name} at {destination.url} to confirm the official policy or next steps."
        )

    return (
        f"I cannot provide a reliable answer for this request because the available information is insufficient or incomplete. "
        f"Please contact {destination.name} at {destination.url} for an official answer and next steps."
    )


def _answer_type_for(
    *,
    issue: str | None = None,
    reason: str | None = None,
    question: str | None = None,
) -> str:
    combined = " ".join(part for part in (issue, reason, question) if part)
    lowered = _normalize_text(combined)
    if "personal" in lowered or "record" in lowered or "eligibility" in lowered or "award" in lowered:
        return "escalation"
    if "conflict" in lowered or "conflicting" in lowered or "unclear" in lowered:
        return "conflict"
    return "insufficient_information"


class EscalationService:
    """Resolve approved university escalation destinations and safe limitation language."""

    DEFAULT_DESTINATIONS = _DEFAULT_DESTINATIONS

    @staticmethod
    def get_approved_destinations() -> tuple[EscalationDestination, ...]:
        return EscalationService.DEFAULT_DESTINATIONS

    @staticmethod
    def lookup_destination(
        question: str | None = None,
        *,
        issue: str | None = None,
        reason: str | None = None,
        category: str | None = None,
        campus: str | None = None,
    ) -> EscalationDestination | None:
        resolved_category = _resolve_category(
            question=question,
            issue=issue,
            reason=reason,
            category=category,
            campus=campus,
        )
        return _destination_for_category(resolved_category)

    @staticmethod
    def build_limitation_message(
        reason: str | None = None,
        *,
        destination: EscalationDestination | None = None,
        question: str | None = None,
        issue: str | None = None,
    ) -> str:
        current_reason = reason or question or issue or "insufficient information"
        return _safe_limit_message(destination, reason=current_reason)

    @staticmethod
    def resolve(
        question: str | None = None,
        *,
        issue: str | None = None,
        reason: str | None = None,
        category: str | None = None,
        campus: str | None = None,
    ) -> EscalationResult:
        destination = EscalationService.lookup_destination(
            question,
            issue=issue,
            reason=reason,
            category=category,
            campus=campus,
        )
        answer_type = _answer_type_for(issue=issue, reason=reason, question=question)
        message = _safe_limit_message(destination, reason=reason)
        return EscalationResult(
            answer_type=answer_type,
            message=message,
            destination=destination,
            escalation_target=destination.name if destination else None,
        )


def lookup_escalation_destination(
    question: str | None = None,
    *,
    issue: str | None = None,
    reason: str | None = None,
    category: str | None = None,
    campus: str | None = None,
) -> EscalationDestination | None:
    return EscalationService.lookup_destination(
        question,
        issue=issue,
        reason=reason,
        category=category,
        campus=campus,
    )


def build_safe_limitation_message(
    reason: str | None = None,
    *,
    destination: EscalationDestination | None = None,
    question: str | None = None,
    issue: str | None = None,
) -> str:
    return EscalationService.build_limitation_message(
        reason,
        destination=destination,
        question=question,
        issue=issue,
    )


safe_limitation_message = build_safe_limitation_message
resolve_escalation_destination = lookup_escalation_destination


__all__ = [
    "EscalationDestination",
    "EscalationResult",
    "EscalationService",
    "build_safe_limitation_message",
    "lookup_escalation_destination",
    "resolve_escalation_destination",
    "safe_limitation_message",
]
