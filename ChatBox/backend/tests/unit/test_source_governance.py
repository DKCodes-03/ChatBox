from __future__ import annotations

from datetime import date, timedelta

from app.models import SourceStatus
from app.services.source_governance import SourceGovernanceService, review_source_status


def test_recent_source_is_active_and_fresh() -> None:
    result = review_source_status({"last_updated": date.today() - timedelta(days=30)})

    assert result.status is SourceStatus.ACTIVE
    assert result.is_fresh is True
    assert "fresh" in result.reason.lower()


def test_stale_source_is_archived() -> None:
    stale_date = date.today() - timedelta(days=900)
    result = review_source_status({"last_updated": stale_date})

    assert result.status is SourceStatus.ARCHIVED
    assert result.is_fresh is False
    assert "stale" in result.reason.lower()


def test_unreviewed_or_conflicting_source_is_disputed() -> None:
    result = review_source_status({"status": "disputed"})
    assert result.status is SourceStatus.DISPUTED
    assert result.is_fresh is False

    disputed = review_source_status({"has_conflict": True, "last_updated": date.today()})
    assert disputed.status is SourceStatus.DISPUTED


def test_service_can_review_source_status() -> None:
    service = SourceGovernanceService()
    result = service.review({"last_updated": date.today(), "reviewed_by": "Registrar"})

    assert result.status is SourceStatus.ACTIVE
    assert result.reviewed_by == "Registrar"
