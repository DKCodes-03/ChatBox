from __future__ import annotations

from datetime import date, timedelta

from app.models import SourceStatus
from app.services.retrieval_service import (
    filter_active_retrieval_candidates,
    normalize_query,
)


def test_normalize_query_cleans_and_lowercases_question() -> None:
    normalized = normalize_query("  What are parking rules on the HAMMOND campus?  ")
    assert normalized == "what are parking rules on the hammond campus"


def test_filter_active_retrieval_candidates_applies_scope_and_freshness() -> None:
    candidates = [
        {
            "id": "a",
            "campus_scope": "hammond",
            "academic_term": "fall",
            "status": SourceStatus.ACTIVE,
            "last_updated": date.today() - timedelta(days=30),
            "content": "Parking permits are required for Hammond students.",
        },
        {
            "id": "b",
            "campus_scope": "westville",
            "academic_term": "fall",
            "status": SourceStatus.ACTIVE,
            "last_updated": date.today() - timedelta(days=30),
            "content": "Parking permits are required for Westville students.",
        },
        {
            "id": "c",
            "campus_scope": "both",
            "academic_term": "spring",
            "status": SourceStatus.ARCHIVED,
            "last_updated": date.today() - timedelta(days=800),
            "content": "Old parking guidance.",
        },
    ]

    filtered = filter_active_retrieval_candidates(
        candidates,
        campus_scope="hammond",
        academic_term="fall",
    )

    assert [item["id"] for item in filtered] == ["a"]
