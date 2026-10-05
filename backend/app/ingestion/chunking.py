"""Semantic evidence chunking with exact MiniLM wordpiece bounds."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import cast
from uuid import UUID, uuid4

from app.ingestion.extract import (
    ExtractedDocument,
    ExtractedLink,
    ExtractedTable,
    ExtractedTableCell,
    ExtractedTableRow,
    ExtractedUnit,
    ExtractedUnitKind,
    TableSection,
)
from app.models.enums import ExtractionStatus
from app.models.sources import Applicability, EvidenceBlock, SourceVersion
from app.retrieval.embeddings import MAX_EVIDENCE_WORDPIECES

WordpieceCounter = Callable[[str], int]

_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")
_REQUIREMENT_LANGUAGE = re.compile(
    r"\b(?:pre-?requisite|co-?requisite|minimum grade|concurrent(?:ly)?|either both|one of)\b",
    re.IGNORECASE,
)


class ChunkingFailureReason(StrEnum):
    """Bounded reasons why evidence chunks cannot be produced safely."""

    INCOMPLETE_EXTRACTION = "incomplete_extraction"
    INVALID_CONTEXT = "invalid_context"
    INVALID_WORDPIECE_COUNT = "invalid_wordpiece_count"
    NO_SEMANTIC_CONTENT = "no_semantic_content"
    PREREQUISITE_GROUP_TOO_LARGE = "prerequisite_group_too_large"
    SEMANTIC_UNIT_TOO_LARGE = "semantic_unit_too_large"
    TABLE_ROW_TOO_LARGE = "table_row_too_large"
    TABLE_STRUCTURE_MISSING = "table_structure_missing"


class ChunkingError(RuntimeError):
    """Base error with a bounded, non-content-bearing reason."""

    def __init__(self, reason: ChunkingFailureReason) -> None:
        super().__init__(reason.value)
        self.reason = reason


class ChunkingInputError(ChunkingError):
    """The source version, extraction, or classification context is invalid."""


class SemanticUnitTooLargeError(ChunkingError):
    """An indivisible semantic unit exceeds the exact wordpiece limit."""

    def __init__(
        self,
        reason: ChunkingFailureReason,
        *,
        wordpiece_count: int,
        maximum: int,
    ) -> None:
        super().__init__(reason)
        self.wordpiece_count = wordpiece_count
        self.maximum = maximum


@dataclass(frozen=True, slots=True)
class ChunkingContext:
    """Source-backed topic and applicability assigned before chunking."""

    topic_key: str
    institution: str = "Purdue University Northwest"
    campus: str | None = None
    student_level: str | None = None
    program: str | None = None
    catalog_year: str | None = None
    term: str | None = None
    session: str | None = None
    scope: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "topic_key", _required_text(self.topic_key, maximum=128))
        object.__setattr__(self, "institution", _required_text(self.institution, maximum=255))
        for name, maximum in (
            ("campus", 128),
            ("student_level", 128),
            ("program", 255),
            ("catalog_year", 32),
            ("term", 64),
            ("session", 64),
        ):
            object.__setattr__(self, name, _optional_text(getattr(self, name), maximum=maximum))
        object.__setattr__(self, "scope", _json_object(self.scope))


@dataclass(frozen=True, slots=True)
class ChunkingResult:
    """Evidence blocks and their source-backed applicability record."""

    blocks: tuple[EvidenceBlock, ...]
    applicability: Applicability
    wordpiece_counts: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class _Draft:
    text: str
    heading_path: tuple[str, ...]
    page: int | None
    anchor: str | None
    structured_content: dict[str, object] | list[object]
    source_unit_indexes: tuple[int, ...]
    protected_reason: ChunkingFailureReason | None = None


class SemanticChunker:
    """Create complete evidence blocks without splitting protected relationships."""

    def __init__(
        self,
        wordpiece_counter: WordpieceCounter,
        *,
        max_wordpieces: int = MAX_EVIDENCE_WORDPIECES,
    ) -> None:
        if not callable(wordpiece_counter):
            raise TypeError("wordpiece_counter must be callable")
        if max_wordpieces != MAX_EVIDENCE_WORDPIECES:
            raise ChunkingInputError(ChunkingFailureReason.INVALID_CONTEXT)
        self._wordpiece_counter = wordpiece_counter
        self._max_wordpieces = max_wordpieces

    def chunk(
        self,
        *,
        version: SourceVersion,
        document: ExtractedDocument,
        context: ChunkingContext,
    ) -> ChunkingResult:
        """Build deterministic blocks; persistence remains the T055 transaction's job."""

        _validate_inputs(version, document, context)
        drafts = self._drafts(document, context)
        if not drafts:
            raise ChunkingInputError(ChunkingFailureReason.NO_SEMANTIC_CONTENT)

        blocks: list[EvidenceBlock] = []
        counts: list[int] = []
        for ordinal, draft in enumerate(drafts):
            count = self._count(draft.heading_path, draft.text)
            if count > self._max_wordpieces:
                raise SemanticUnitTooLargeError(
                    draft.protected_reason or ChunkingFailureReason.SEMANTIC_UNIT_TOO_LARGE,
                    wordpiece_count=count,
                    maximum=self._max_wordpieces,
                )
            structured = _with_chunk_metadata(
                draft.structured_content,
                wordpiece_count=count,
                source_unit_indexes=draft.source_unit_indexes,
            )
            blocks.append(
                EvidenceBlock(
                    id=uuid4(),
                    version_id=version.id,
                    ordinal=ordinal,
                    heading_path=list(draft.heading_path),
                    page=draft.page,
                    anchor=draft.anchor,
                    text=draft.text,
                    structured_content=structured,
                    topic_key=context.topic_key,
                    scope=dict(context.scope),
                )
            )
            counts.append(count)

        applicability = Applicability(
            version_id=version.id,
            topic=context.topic_key,
            institution=context.institution,
            campus=context.campus,
            student_level=context.student_level,
            program=context.program,
            catalog_year=context.catalog_year,
            term=context.term,
            session=context.session,
            evidence_block_ids=[block.id for block in blocks],
        )
        return ChunkingResult(
            blocks=tuple(blocks),
            applicability=applicability,
            wordpiece_counts=tuple(counts),
        )

    def _drafts(
        self,
        document: ExtractedDocument,
        context: ChunkingContext,
    ) -> tuple[_Draft, ...]:
        table_drafts, table_unit_indexes = self._table_drafts(document)
        attached_footnote_indexes = frozenset(
            index
            for index, unit in enumerate(document.units)
            if unit.kind is ExtractedUnitKind.FOOTNOTE
            and any(
                unit.text in table.footnotes
                and unit.page == table.page
                and unit.heading_path == table.heading_path
                for table in document.tables
            )
        )
        prose_units = tuple(
            (index, unit)
            for index, unit in enumerate(document.units)
            if index not in table_unit_indexes
            and index not in attached_footnote_indexes
            and unit.kind is not ExtractedUnitKind.HEADING
        )
        prose_drafts = self._prose_drafts(prose_units, context)
        combined = [*table_drafts, *prose_drafts]
        combined.sort(key=lambda draft: min(draft.source_unit_indexes))
        return tuple(combined)

    def _table_drafts(
        self,
        document: ExtractedDocument,
    ) -> tuple[list[_Draft], frozenset[int]]:
        drafts: list[_Draft] = []
        table_unit_indexes: set[int] = set()
        units_by_row: dict[tuple[int, int], tuple[int, ExtractedUnit]] = {}
        for index, unit in enumerate(document.units):
            if unit.table_index is None:
                continue
            table_unit_indexes.add(index)
            if unit.table_row_index is not None:
                units_by_row[(unit.table_index, unit.table_row_index)] = (index, unit)

        for table_index, table in enumerate(document.tables):
            header_rows = tuple(row for row in table.rows if _is_header_row(row))
            body_rows = tuple(
                (row_index, row)
                for row_index, row in enumerate(table.rows)
                if row.section is not TableSection.FOOT and not _is_header_row(row)
            )
            if not header_rows or not body_rows:
                raise ChunkingInputError(ChunkingFailureReason.TABLE_STRUCTURE_MISSING)
            for row_index, row in body_rows:
                unit_entry = units_by_row.get((table_index, row_index))
                if unit_entry is None:
                    raise ChunkingInputError(ChunkingFailureReason.TABLE_STRUCTURE_MISSING)
                unit_index, unit = unit_entry
                text = _table_embedding_text(table, header_rows, row)
                structured = _table_structured_content(
                    table_index=table_index,
                    table=table,
                    header_rows=header_rows,
                    row_index=row_index,
                    row=row,
                    unit=unit,
                )
                drafts.append(
                    _Draft(
                        text=text,
                        heading_path=table.heading_path or unit.heading_path,
                        page=_row_anchor_page(table, unit),
                        anchor=row.anchor or table.anchor or unit.anchor,
                        structured_content=structured,
                        source_unit_indexes=(unit_index,),
                        protected_reason=ChunkingFailureReason.TABLE_ROW_TOO_LARGE,
                    )
                )
        return drafts, frozenset(table_unit_indexes)

    def _prose_drafts(
        self,
        indexed_units: Sequence[tuple[int, ExtractedUnit]],
        context: ChunkingContext,
    ) -> list[_Draft]:
        groups: list[list[tuple[int, ExtractedUnit]]] = []
        current: list[tuple[int, ExtractedUnit]] = []
        current_key: tuple[tuple[str, ...], int | None, str | None] | None = None
        seen: set[tuple[object, ...]] = set()
        for index, unit in indexed_units:
            signature = (
                unit.kind,
                unit.text,
                unit.heading_path,
                unit.page,
                unit.anchor,
            )
            if signature in seen:
                continue
            seen.add(signature)
            key = (unit.heading_path, unit.page, unit.anchor)
            if current and key != current_key:
                groups.append(current)
                current = []
            current.append((index, unit))
            current_key = key
        if current:
            groups.append(current)

        drafts: list[_Draft] = []
        for group in groups:
            if _is_prerequisite_group(group, context.topic_key):
                draft = _prose_draft(
                    group,
                    content_type="prerequisite_group",
                    protected_reason=ChunkingFailureReason.PREREQUISITE_GROUP_TOO_LARGE,
                )
                self._ensure_fits(draft)
                drafts.append(draft)
            else:
                drafts.extend(self._bounded_prose_group(group))
        return drafts

    def _bounded_prose_group(
        self,
        group: Sequence[tuple[int, ExtractedUnit]],
    ) -> list[_Draft]:
        drafts: list[_Draft] = []
        current: list[tuple[int, ExtractedUnit]] = []
        for item in group:
            candidate = [*current, item]
            draft = _prose_draft(candidate, content_type="semantic_text")
            if self._fits(draft):
                current = candidate
                continue
            if current:
                drafts.append(_prose_draft(current, content_type="semantic_text"))
                current = []
            single = _prose_draft((item,), content_type="semantic_text")
            if self._fits(single):
                current = [item]
                continue
            drafts.extend(self._split_unit(item))
        if current:
            drafts.append(_prose_draft(current, content_type="semantic_text"))
        return drafts

    def _split_unit(self, item: tuple[int, ExtractedUnit]) -> list[_Draft]:
        index, unit = item
        sentences = tuple(
            part.strip() for part in _SENTENCE_BOUNDARY.split(unit.text) if part.strip()
        )
        if len(sentences) < 2:
            draft = _prose_draft((item,), content_type="semantic_text")
            self._ensure_fits(draft)
            return [draft]

        drafts: list[_Draft] = []
        current: list[str] = []
        for sentence in sentences:
            candidate_text = " ".join((*current, sentence))
            draft = _text_fragment_draft(index, unit, candidate_text)
            if self._fits(draft):
                current.append(sentence)
                continue
            if current:
                drafts.append(_text_fragment_draft(index, unit, " ".join(current)))
                current = []
            single = _text_fragment_draft(index, unit, sentence)
            self._ensure_fits(single)
            current.append(sentence)
        if current:
            drafts.append(_text_fragment_draft(index, unit, " ".join(current)))
        return drafts

    def _fits(self, draft: _Draft) -> bool:
        return self._count(draft.heading_path, draft.text) <= self._max_wordpieces

    def _ensure_fits(self, draft: _Draft) -> None:
        count = self._count(draft.heading_path, draft.text)
        if count > self._max_wordpieces:
            raise SemanticUnitTooLargeError(
                draft.protected_reason or ChunkingFailureReason.SEMANTIC_UNIT_TOO_LARGE,
                wordpiece_count=count,
                maximum=self._max_wordpieces,
            )

    def _count(self, heading_path: Sequence[str], text: str) -> int:
        complete_text = embedding_text(heading_path=heading_path, text=text)
        try:
            count = self._wordpiece_counter(complete_text)
        except ChunkingError:
            raise
        except Exception:
            raise ChunkingInputError(ChunkingFailureReason.INVALID_WORDPIECE_COUNT) from None
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise ChunkingInputError(ChunkingFailureReason.INVALID_WORDPIECE_COUNT)
        return count


def embedding_text(*, heading_path: Sequence[str], text: str) -> str:
    """Return exactly the heading-plus-body text whose wordpieces are bounded."""

    headings = tuple(_required_text(heading, maximum=None) for heading in heading_path)
    body = _required_text(text, maximum=None)
    return "\n".join((*headings, body))


def _validate_inputs(
    version: SourceVersion,
    document: ExtractedDocument,
    context: ChunkingContext,
) -> None:
    if not isinstance(version, SourceVersion) or not isinstance(version.id, UUID):
        raise ChunkingInputError(ChunkingFailureReason.INVALID_CONTEXT)
    if not isinstance(document, ExtractedDocument) or not isinstance(context, ChunkingContext):
        raise ChunkingInputError(ChunkingFailureReason.INVALID_CONTEXT)
    if (
        version.extraction_status is not ExtractionStatus.COMPLETE
        or not document.complete
        or bool(document.issues)
    ):
        raise ChunkingInputError(ChunkingFailureReason.INCOMPLETE_EXTRACTION)
    if version.parser_version != document.parser_version:
        raise ChunkingInputError(ChunkingFailureReason.INVALID_CONTEXT)


def _prose_draft(
    group: Sequence[tuple[int, ExtractedUnit]],
    *,
    content_type: str,
    protected_reason: ChunkingFailureReason | None = None,
) -> _Draft:
    first = group[0][1]
    units = [unit for _, unit in group]
    return _Draft(
        text="\n".join(unit.text for unit in units),
        heading_path=first.heading_path,
        page=first.page,
        anchor=first.anchor,
        structured_content={
            "type": content_type,
            "unit_kinds": [unit.kind.value for unit in units],
            "links": _links_json(link for unit in units for link in unit.links),
            "campus_labels": list(_unique(label for unit in units for label in unit.campus_labels)),
            "catalog_context": list(
                _unique(label for unit in units for label in unit.catalog_context)
            ),
        },
        source_unit_indexes=tuple(index for index, _ in group),
        protected_reason=protected_reason,
    )


def _text_fragment_draft(index: int, unit: ExtractedUnit, text: str) -> _Draft:
    return _Draft(
        text=text,
        heading_path=unit.heading_path,
        page=unit.page,
        anchor=unit.anchor,
        structured_content={
            "type": "semantic_text",
            "unit_kinds": [unit.kind.value],
            "links": _links_json(unit.links),
            "campus_labels": list(unit.campus_labels),
            "catalog_context": list(unit.catalog_context),
        },
        source_unit_indexes=(index,),
    )


def _is_prerequisite_group(
    group: Sequence[tuple[int, ExtractedUnit]],
    topic_key: str,
) -> bool:
    folded_topic = topic_key.casefold().replace("-", "_")
    if "prerequisite" in folded_topic or "corequisite" in folded_topic:
        return True
    return any(_REQUIREMENT_LANGUAGE.search(unit.text) for _, unit in group)


def _is_header_row(row: ExtractedTableRow) -> bool:
    return row.section is TableSection.HEAD or all(cell.is_header for cell in row.cells)


def _table_embedding_text(
    table: ExtractedTable,
    header_rows: Sequence[ExtractedTableRow],
    row: ExtractedTableRow,
) -> str:
    parts: list[str] = []
    if table.caption:
        parts.append(f"Table: {table.caption}")
    parts.extend(f"Column headers: {header.text}" for header in header_rows)
    parts.append(f"Row: {row.text}")
    parts.extend(f"Footnote: {note}" for note in table.footnotes)
    return "\n".join(parts)


def _table_structured_content(
    *,
    table_index: int,
    table: ExtractedTable,
    header_rows: Sequence[ExtractedTableRow],
    row_index: int,
    row: ExtractedTableRow,
    unit: ExtractedUnit,
) -> dict[str, object]:
    return {
        "type": "table_row",
        "table_index": table_index,
        "row_index": row_index,
        "caption": table.caption,
        "headers": [_row_json(header) for header in header_rows],
        "row": _row_json(row),
        "footnotes": list(table.footnotes),
        "links": _links_json(unit.links),
        "campus_labels": list(unit.campus_labels),
        "catalog_context": list(unit.catalog_context),
    }


def _row_json(row: ExtractedTableRow) -> dict[str, object]:
    return {
        "section": row.section.value,
        "anchor": row.anchor,
        "cells": [_cell_json(cell) for cell in row.cells],
    }


def _cell_json(cell: ExtractedTableCell) -> dict[str, object]:
    return {
        "text": cell.text,
        "is_header": cell.is_header,
        "scope": cell.scope,
        "colspan": cell.colspan,
        "rowspan": cell.rowspan,
        "links": _links_json(cell.links),
    }


def _links_json(links: Iterable[ExtractedLink]) -> list[dict[str, object]]:
    return [
        {
            "text": link.text,
            "url": link.url,
            "page": link.page,
            "source_anchor": link.source_anchor,
        }
        for link in links
    ]


def _row_anchor_page(table: ExtractedTable, unit: ExtractedUnit) -> int | None:
    return table.page if table.page is not None else unit.page


def _with_chunk_metadata(
    structured: dict[str, object] | list[object],
    *,
    wordpiece_count: int,
    source_unit_indexes: Sequence[int],
) -> dict[str, object] | list[object]:
    if isinstance(structured, dict):
        return {
            **structured,
            "embedding_wordpiece_count": wordpiece_count,
            "source_unit_indexes": list(source_unit_indexes),
        }
    return [
        *structured,
        {
            "embedding_wordpiece_count": wordpiece_count,
            "source_unit_indexes": list(source_unit_indexes),
        },
    ]


def _json_object(value: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise ChunkingInputError(ChunkingFailureReason.INVALID_CONTEXT)
    try:
        encoded = json.dumps(dict(value), allow_nan=False, separators=(",", ":"))
        decoded = json.loads(encoded)
    except (TypeError, ValueError):
        raise ChunkingInputError(ChunkingFailureReason.INVALID_CONTEXT) from None
    if not isinstance(decoded, dict):
        raise ChunkingInputError(ChunkingFailureReason.INVALID_CONTEXT)
    return cast(dict[str, object], decoded)


def _required_text(value: str, *, maximum: int | None) -> str:
    if not isinstance(value, str):
        raise ChunkingInputError(ChunkingFailureReason.INVALID_CONTEXT)
    cleaned = " ".join(value.split())
    if not cleaned or (maximum is not None and len(cleaned) > maximum):
        raise ChunkingInputError(ChunkingFailureReason.INVALID_CONTEXT)
    return cleaned


def _optional_text(value: str | None, *, maximum: int) -> str | None:
    if value is None:
        return None
    return _required_text(value, maximum=maximum)


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


__all__ = [
    "ChunkingContext",
    "ChunkingError",
    "ChunkingFailureReason",
    "ChunkingInputError",
    "ChunkingResult",
    "SemanticChunker",
    "SemanticUnitTooLargeError",
    "WordpieceCounter",
    "embedding_text",
]
