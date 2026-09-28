from __future__ import annotations

from dataclasses import dataclass

from ingestion.extract_content import ExtractedContent, ExtractedElement


@dataclass(frozen=True)
class Chunk:
    heading: str | None
    page_ref: str | None
    content: str


def _render_element(element: ExtractedElement) -> str:
    text = element.text.strip()
    if not text:
        return ""
    if element.kind == "list_item":
        return f"- {text}"
    if element.kind == "table_row":
        return text
    return text


def chunk_content(extracted: ExtractedContent, *, max_chars: int = 1200) -> list[Chunk]:
    """Group extracted elements into structure-preserving chunks.

    Headings remain metadata, while related paragraphs, list items, dates,
    conditions, and table rows remain together in the same chunk unless the
    section changes or the chunk exceeds the configured size cap.
    """
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")

    chunks: list[Chunk] = []
    current_heading: str | None = None
    current_page_ref: str | None = None
    lines: list[str] = []

    def flush_current() -> None:
        nonlocal current_heading, current_page_ref, lines
        if not lines:
            return
        content = "\n".join(line for line in lines if line.strip()).strip()
        if content:
            chunks.append(Chunk(heading=current_heading, page_ref=current_page_ref, content=content))
        lines = []

    for element in extracted.elements:
        if element.kind == "heading":
            if lines:
                flush_current()
            current_heading = element.text.strip() or current_heading
            current_page_ref = element.page_ref or current_page_ref
            continue

        if current_heading is None:
            current_heading = extracted.title.strip() if extracted.title else None

        if element.page_ref:
            current_page_ref = element.page_ref

        rendered = _render_element(element)
        if rendered:
            lines.append(rendered)

        if len("\n".join(lines)) > max_chars and len(lines) > 1:
            flush_current()
            if current_heading is not None and not any(
                line == current_heading for line in lines
            ):
                lines = []

    if lines:
        flush_current()

    if not chunks and extracted.title:
        body = "\n".join(
            _render_element(element)
            for element in extracted.elements
            if element.kind != "heading" and _render_element(element)
        ).strip()
        if body:
            chunks.append(Chunk(heading=extracted.title, page_ref=None, content=body))

    return chunks


__all__ = ["Chunk", "chunk_content"]
