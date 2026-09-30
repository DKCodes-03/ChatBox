from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.models import CorpusBuild, CorpusBuildStatus
from ingestion.build_corpus import select_approved_sources, validate_build_requirements


def test_validate_build_requirements_rejects_incomplete_construction() -> None:
    build = CorpusBuild(
        id=uuid4(),
        version="candidate-1",
        embedding_model="test-model",
        embedding_dimensions=3,
        source_count=0,
        chunk_count=0,
        status=CorpusBuildStatus.BUILDING,
        started_at=datetime.now(UTC),
    )

    with pytest.raises(ValueError, match="at least one source"):
        validate_build_requirements(build)


def test_select_approved_sources_skips_pending_and_requires_recent_review() -> None:
    today = datetime.now(UTC).date()
    sources = [
        {"id": "pending", "review_status": "pending_initial_review"},
        {
            "id": "approved",
            "review_status": "active",
            "reviewer": "Registrar",
            "last_reviewed": today.isoformat(),
            "title": "Calendar",
            "url": "https://www.pnw.edu/academic-calendar/",
            "source_type": "webpage",
        },
    ]

    approved, skipped = select_approved_sources(
        sources,
        {"default_cadence_days": 30},
        today=today,
    )

    assert [source["id"] for source in approved] == ["approved"]
    assert skipped == ["pending"]


def test_select_approved_sources_rejects_overdue_review() -> None:
    today = datetime.now(UTC).date()
    source = {
        "id": "overdue",
        "review_status": "approved",
        "reviewer": "Registrar",
        "last_reviewed": (today - timedelta(days=31)).isoformat(),
        "title": "Calendar",
        "url": "https://www.pnw.edu/academic-calendar/",
        "source_type": "webpage",
    }

    with pytest.raises(ValueError, match="reviewed within its 30-day cadence"):
        select_approved_sources([source], {"default_cadence_days": 30}, today=today)
