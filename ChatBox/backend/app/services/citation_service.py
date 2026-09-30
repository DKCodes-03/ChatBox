from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.schemas import CitationResponse

DEFAULT_MAX_CITATIONS = 3


def _read_field(candidate: Any, *names: str) -> Any:
    if candidate is None:
        return None
    if isinstance(candidate, Mapping):
        for name in names:
            if name in candidate:
                return candidate[name]
        return None
    for name in names:
        if hasattr(candidate, name):
            return getattr(candidate, name)
    return None


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    text = " ".join(text.split())
    return text


def _build_snippet(hit: Mapping[str, Any] | Any) -> str:
    heading = _normalize_text(_read_field(hit, "heading", "section_title", "title_heading"))
    content = _normalize_text(_read_field(hit, "content", "snippet", "quote_snippet", "excerpt", "text"))
    if content:
        snippet = content
        if heading and heading.lower() not in snippet.lower():
            snippet = f"{heading}: {snippet}"
        if len(snippet) > 260:
            snippet = f"{snippet[:257].rstrip()}..."
        return snippet
    if heading:
        return heading
    return "Official university source evidence."


def assemble_citations(
    hits: Sequence[Mapping[str, Any] | Any] | None,
    *,
    max_citations: int = DEFAULT_MAX_CITATIONS,
) -> list[CitationResponse]:
    """Convert retrieval hits into direct-source citations for grounded answers."""
    if not hits:
        return []

    citations: list[CitationResponse] = []
    seen_urls: set[str] = set()
    limit = max(1, max_citations)

    for hit in hits:
        url_value = _read_field(hit, "url", "document_url", "link_url", "source_url")
        if url_value is None:
            continue
        url_text = _normalize_text(url_value)
        if not url_text:
            continue
        if url_text in seen_urls:
            continue
        seen_urls.add(url_text)

        title = _normalize_text(_read_field(hit, "title", "document_title", "source_title"))
        if not title:
            title = _normalize_text(_read_field(hit, "heading", "section_title", "page_title"))
        if not title:
            title = "Official source"

        snippet = _build_snippet(hit)
        citations.append(
            CitationResponse(
                title=title,
                url=url_text,
                snippet=snippet,
            )
        )
        if len(citations) >= limit:
            break

    return citations


class CitationService:
    """Assemble official citations for supported answers using direct source links."""

    def __init__(self, *, max_citations: int = DEFAULT_MAX_CITATIONS) -> None:
        self.max_citations = max(1, max_citations)

    def assemble(self, hits: Sequence[Mapping[str, Any] | Any] | None) -> list[CitationResponse]:
        return assemble_citations(hits, max_citations=self.max_citations)


__all__ = ["CitationService", "DEFAULT_MAX_CITATIONS", "assemble_citations"]
