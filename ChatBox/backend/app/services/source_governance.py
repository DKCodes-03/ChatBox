from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from app.models import SourceStatus

_DEFAULT_FRESHNESS_WINDOW_DAYS = 365
_DEFAULT_ARCHIVE_WINDOW_DAYS = 730


@dataclass(frozen=True, slots=True)
class SourceReviewResult:
    status: SourceStatus
    is_fresh: bool
    reason: str
    last_updated: date | None = None
    reviewed_by: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "is_fresh": self.is_fresh,
            "reason": self.reason,
            "last_updated": self.last_updated.isoformat() if self.last_updated else None,
            "reviewed_by": self.reviewed_by,
        }


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


def _coerce_string(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or None
    return str(value)


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


def _normalize_status(value: Any) -> SourceStatus | None:
    if value is None:
        return None
    if isinstance(value, SourceStatus):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if not normalized:
            return None
        try:
            return SourceStatus(normalized)
        except ValueError:
            aliases = {
                "approved": SourceStatus.ACTIVE,
                "current": SourceStatus.ACTIVE,
                "verified": SourceStatus.ACTIVE,
                "reviewed": SourceStatus.ACTIVE,
                "expired": SourceStatus.ARCHIVED,
                "obsolete": SourceStatus.ARCHIVED,
                "needs_review": SourceStatus.DISPUTED,
                "unreviewed": SourceStatus.DISPUTED,
                "conflict": SourceStatus.DISPUTED,
                "conflicted": SourceStatus.DISPUTED,
            }
            return aliases.get(normalized)
    return None


def _source_has_conflict(source: Any) -> bool:
    for name in (
        "has_conflict",
        "conflict",
        "material_conflict",
        "conflicting_source",
        "conflicting_sources",
    ):
        value = _read_field(source, name)
        if isinstance(value, bool):
            if value:
                return True
        elif value is not None:
            return bool(value)
    return False


def review_source_status(source: Any) -> SourceReviewResult:
    """Review source freshness and assign a governance status.

    The source may be a mapping or an object with matching attributes. A source with
    a material conflict or missing evidence of review is marked as disputed. A stale
    source is archived, while a recent or recently reviewed source remains active.
    """

    if source is None:
        return SourceReviewResult(
            status=SourceStatus.DISPUTED,
            is_fresh=False,
            reason="Source metadata is missing; this source cannot be approved.",
        )

    last_updated = _coerce_date(_read_field(source, "last_updated", "updated_at", "review_date"))
    reviewed_by = _coerce_string(_read_field(source, "reviewed_by", "review_owner", "owner"))
    explicit_status = _normalize_status(_read_field(source, "status", "source_status", "review_status"))

    if explicit_status is SourceStatus.ARCHIVED:
        return SourceReviewResult(
            status=SourceStatus.ARCHIVED,
            is_fresh=False,
            reason="Source is explicitly archived and should not be used for current guidance.",
            last_updated=last_updated,
            reviewed_by=reviewed_by,
        )

    if explicit_status is SourceStatus.DISPUTED:
        return SourceReviewResult(
            status=SourceStatus.DISPUTED,
            is_fresh=False,
            reason="Source is disputed and must be re-reviewed before approval.",
            last_updated=last_updated,
            reviewed_by=reviewed_by,
        )

    if explicit_status is SourceStatus.ACTIVE:
        if last_updated is None and reviewed_by is None:
            return SourceReviewResult(
                status=SourceStatus.DISPUTED,
                is_fresh=False,
                reason="Active status is not supported without a freshness date or reviewer.",
                last_updated=None,
                reviewed_by=None,
            )
        age_days = (date.today() - last_updated).days if last_updated else None
        if age_days is not None and age_days > _DEFAULT_ARCHIVE_WINDOW_DAYS:
            return SourceReviewResult(
                status=SourceStatus.ARCHIVED,
                is_fresh=False,
                reason=f"Source is stale; it was last updated {age_days} days ago.",
                last_updated=last_updated,
                reviewed_by=reviewed_by,
            )
        return SourceReviewResult(
            status=SourceStatus.ACTIVE,
            is_fresh=True,
            reason=(
                "Source is fresh and has current review metadata."
                if last_updated is not None
                else "Source is current and has a clear review owner."
            ),
            last_updated=last_updated,
            reviewed_by=reviewed_by,
        )

    if _source_has_conflict(source):
        return SourceReviewResult(
            status=SourceStatus.DISPUTED,
            is_fresh=False,
            reason="Source content conflicts with another approved source and requires manual review.",
            last_updated=last_updated,
            reviewed_by=reviewed_by,
        )

    if last_updated is None and reviewed_by is None:
        return SourceReviewResult(
            status=SourceStatus.DISPUTED,
            is_fresh=False,
            reason="Source freshness cannot be established; it lacks a review date or reviewer.",
            last_updated=None,
            reviewed_by=None,
        )

    if last_updated is not None:
        age_days = (date.today() - last_updated).days
        if age_days > _DEFAULT_ARCHIVE_WINDOW_DAYS:
            return SourceReviewResult(
                status=SourceStatus.ARCHIVED,
                is_fresh=False,
                reason=f"Source is stale; it was last updated {age_days} days ago.",
                last_updated=last_updated,
                reviewed_by=reviewed_by,
            )

        if age_days <= _DEFAULT_FRESHNESS_WINDOW_DAYS:
            return SourceReviewResult(
                status=SourceStatus.ACTIVE,
                is_fresh=True,
                reason=f"Source is fresh; last updated {last_updated.isoformat()}.",
                last_updated=last_updated,
                reviewed_by=reviewed_by,
            )

        return SourceReviewResult(
            status=SourceStatus.ARCHIVED,
            is_fresh=False,
            reason=f"Source has exceeded the freshness window ({_DEFAULT_FRESHNESS_WINDOW_DAYS} days).",
            last_updated=last_updated,
            reviewed_by=reviewed_by,
        )

    return SourceReviewResult(
        status=SourceStatus.ACTIVE,
        is_fresh=True,
        reason="Source is approved because it has a clear review owner.",
        last_updated=last_updated,
        reviewed_by=reviewed_by,
    )


class SourceGovernanceService:
    """Review and classify source documents according to approval and freshness rules."""

    def __init__(self, freshness_window_days: int = _DEFAULT_FRESHNESS_WINDOW_DAYS) -> None:
        self.freshness_window_days = freshness_window_days

    def review(self, source: Any) -> SourceReviewResult:
        return review_source_status(source)

    def review_many(self, sources: Sequence[Any]) -> list[SourceReviewResult]:
        return [self.review(source) for source in sources]

    def filter_active(self, sources: Sequence[Any]) -> list[SourceReviewResult]:
        return [result for result in self.review_many(sources) if result.status is SourceStatus.ACTIVE]


def review_source(source: Any) -> SourceReviewResult:
    return review_source_status(source)
