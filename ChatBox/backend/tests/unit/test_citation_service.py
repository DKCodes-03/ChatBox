from __future__ import annotations

from app.schemas import CitationResponse
from app.services.citation_service import assemble_citations


def test_assemble_citations_creates_direct_links_and_snippets() -> None:
    hits = [
        {
            "document_title": "Registration Calendar",
            "document_url": "https://www.pnw.edu/registration-calendar",
            "heading": "Priority registration",
            "content": "Priority registration for spring opens on October 1 and closes on November 15.",
        },
        {
            "document_title": "Registration Calendar",
            "document_url": "https://www.pnw.edu/registration-calendar",
            "heading": "Priority registration",
            "content": "Priority registration for spring opens on October 1 and closes on November 15.",
        },
    ]

    citations = assemble_citations(hits, max_citations=3)

    assert citations
    assert len(citations) == 1
    assert citations[0].title == "Registration Calendar"
    assert str(citations[0].url) == "https://www.pnw.edu/registration-calendar"
    assert "October 1" in citations[0].snippet


def test_assemble_citations_uses_explicit_title_and_snippet_when_available() -> None:
    hits = [
        {
            "title": "Academic Catalog",
            "url": "https://catalog.pnw.edu/undergraduate/requirements",
            "content": "Students must complete the prerequisites before enrolling in upper-level courses.",
        }
    ]

    citations = assemble_citations(hits)

    assert citations[0] == CitationResponse(
        title="Academic Catalog",
        url="https://catalog.pnw.edu/undergraduate/requirements",
        snippet="Students must complete the prerequisites before enrolling in upper-level courses.",
    )
