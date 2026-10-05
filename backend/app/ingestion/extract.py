"""Structure-preserving extraction for discovered public HTML and PDF documents."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from io import BytesIO
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Tag
from bs4.element import NavigableString
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.ingestion.discovery import DiscoveredDocument
from app.models.enums import MediaType

PARSER_VERSION = "pnw-structured-extractor-v1"
_CONTENT_TAGS = frozenset({"p", "li", "blockquote", "dt", "dd"})
_CONTAINER_TEXT_TAGS = frozenset({"address", "article", "div", "pre", "section"})
_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6", "summary"})
_BLOCK_TAGS = (
    _CONTENT_TAGS
    | _CONTAINER_TEXT_TAGS
    | _HEADING_TAGS
    | frozenset({"details", "ol", "table", "ul"})
)
_REMOVED_TAGS = ("script", "style", "noscript", "template", "svg", "canvas", "form")
_CONTROL_TAGS = ("button", "input", "select", "textarea")
_CAMPUS_RE = re.compile(r"\b(Hammond|Westville)\b", re.IGNORECASE)
_CATALOG_PATTERNS = (
    re.compile(
        r"\b20\d{2}\s*[-\u2013\u2014/]\s*(?:20)?\d{2}"
        r"(?:\s+(?:undergraduate|graduate)\s+catalog)?\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:undergraduate|graduate)\s+catalog"
        r"(?:\s+(?:for\s+)?20\d{2}\s*[-\u2013\u2014/]\s*(?:20)?\d{2})?\b",
        re.IGNORECASE,
    ),
)
_FOOTNOTE_TOKEN_RE = re.compile(r"(?:footnote|endnote|table[-_ ]?note|^note$)", re.IGNORECASE)
_PDF_FOOTNOTE_RE = re.compile(r"^(?:[*\u2020\u2021]|\[\d{1,3}\])\s*")
_PDF_TABLE_SEPARATOR_RE = re.compile(r"\s{2,}")


class ExtractionFailureReason(StrEnum):
    """Bounded reasons that make an extracted document incomplete."""

    EMPTY_DOCUMENT = "empty_document"
    UNREADABLE_PDF = "unreadable_pdf"
    ENCRYPTED_PDF = "encrypted_pdf"
    PDF_PAGE_UNREADABLE = "pdf_page_unreadable"
    MALFORMED_TABLE = "malformed_table"
    MISSING_EXPANDABLE_CONTENT = "missing_expandable_content"


class ExtractedUnitKind(StrEnum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST_ITEM = "list_item"
    TABLE_ROW = "table_row"
    FOOTNOTE = "footnote"


class TableSection(StrEnum):
    HEAD = "head"
    BODY = "body"
    FOOT = "foot"


class ExtractionInputError(ValueError):
    """The caller supplied an unsupported extraction input."""


@dataclass(frozen=True, slots=True)
class ExtractedLink:
    """A link and the exact source location where it appeared."""

    text: str
    url: str
    page: int | None = None
    source_anchor: str | None = None


@dataclass(frozen=True, slots=True)
class ExtractedTableCell:
    text: str
    is_header: bool
    scope: str | None
    colspan: int
    rowspan: int
    links: tuple[ExtractedLink, ...] = ()


@dataclass(frozen=True, slots=True)
class ExtractedTableRow:
    cells: tuple[ExtractedTableCell, ...]
    section: TableSection
    anchor: str | None

    @property
    def text(self) -> str:
        return " | ".join(cell.text for cell in self.cells)


@dataclass(frozen=True, slots=True)
class ExtractedTable:
    caption: str | None
    heading_path: tuple[str, ...]
    page: int | None
    anchor: str | None
    rows: tuple[ExtractedTableRow, ...]
    footnotes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ExtractedUnit:
    """One source-locatable semantic unit before T054 performs final chunking."""

    kind: ExtractedUnitKind
    text: str
    heading_path: tuple[str, ...]
    page: int | None
    anchor: str | None
    links: tuple[ExtractedLink, ...] = ()
    campus_labels: tuple[str, ...] = ()
    catalog_context: tuple[str, ...] = ()
    table_index: int | None = None
    table_row_index: int | None = None


@dataclass(frozen=True, slots=True)
class ExtractionIssue:
    reason: ExtractionFailureReason
    page: int | None = None
    anchor: str | None = None


@dataclass(frozen=True, slots=True)
class ExtractedDocument:
    canonical_url: str
    title: str
    media_type: MediaType
    parser_version: str
    units: tuple[ExtractedUnit, ...]
    tables: tuple[ExtractedTable, ...]
    links: tuple[ExtractedLink, ...]
    campus_labels: tuple[str, ...]
    catalog_context: tuple[str, ...]
    issues: tuple[ExtractionIssue, ...]
    complete: bool

    @property
    def text(self) -> str:
        """Return readable extracted text while retaining structure in the typed fields."""

        return "\n\n".join(unit.text for unit in self.units)


class StructuredExtractor:
    """Extract structure without deciding source eligibility or applicability."""

    def extract(self, document: DiscoveredDocument) -> ExtractedDocument:
        if not isinstance(document, DiscoveredDocument):
            raise ExtractionInputError("a discovered document is required")
        if document.media_type is MediaType.HTML:
            return self._extract_html(document)
        if document.media_type is MediaType.PDF:
            return self._extract_pdf(document)
        raise ExtractionInputError("the discovered media type is unsupported")

    def _extract_html(self, document: DiscoveredDocument) -> ExtractedDocument:
        soup = BeautifulSoup(document.content, "html.parser")
        title = _html_title(soup) or document.title
        root = soup.find("main") or soup.find("article") or soup.body or soup
        issues = _expandable_issues(root, soup)

        for unwanted in root.find_all(_REMOVED_TAGS):
            unwanted.decompose()
        for control in root.find_all(_CONTROL_TAGS):
            control.decompose()
        for navigation in root.find_all(("nav", "aside")):
            navigation.decompose()

        units: list[ExtractedUnit] = []
        tables: list[ExtractedTable] = []
        heading_stack: list[tuple[int, str]] = []

        root_text = _direct_container_text(root)
        if root_text:
            units.append(
                _html_unit(
                    ExtractedUnitKind.PARAGRAPH,
                    root_text,
                    (),
                    root,
                    _nearest_anchor(root),
                    document.canonical_url,
                    direct_links=True,
                )
            )

        for element in root.find_all(
            (*_HEADING_TAGS, *_CONTENT_TAGS, *_CONTAINER_TEXT_TAGS, "table")
        ):
            if not isinstance(element, Tag) or _inside_excluded_container(element):
                continue
            anchor = _nearest_anchor(element)

            if element.name in _HEADING_TAGS:
                text = _element_text(element)
                if not text:
                    continue
                if element.name == "summary":
                    path = (*tuple(item[1] for item in heading_stack), text)
                    units.append(
                        _html_unit(
                            ExtractedUnitKind.HEADING,
                            text,
                            path,
                            element,
                            anchor,
                            document.canonical_url,
                        )
                    )
                    continue
                level = int(element.name[1])
                while heading_stack and heading_stack[-1][0] >= level:
                    heading_stack.pop()
                heading_stack.append((level, text))
                path = tuple(item[1] for item in heading_stack)
                units.append(
                    _html_unit(
                        ExtractedUnitKind.HEADING,
                        text,
                        path,
                        element,
                        anchor,
                        document.canonical_url,
                    )
                )
                continue

            heading_path = tuple(item[1] for item in heading_stack)
            if element.name in _CONTAINER_TEXT_TAGS:
                text = _direct_container_text(element)
                if text:
                    units.append(
                        _html_unit(
                            ExtractedUnitKind.PARAGRAPH,
                            text,
                            heading_path,
                            element,
                            anchor,
                            document.canonical_url,
                            direct_links=True,
                        )
                    )
                continue
            if element.name == "table":
                table, table_units, table_issues = _extract_html_table(
                    element,
                    document.canonical_url,
                    heading_path,
                    len(tables),
                )
                tables.append(table)
                units.extend(table_units)
                issues.extend(table_issues)
                continue

            text = _element_text(element, exclude_nested_lists=element.name == "li")
            if not text:
                continue
            kind = (
                ExtractedUnitKind.FOOTNOTE
                if _is_footnote(element)
                else ExtractedUnitKind.LIST_ITEM
                if element.name == "li"
                else ExtractedUnitKind.PARAGRAPH
            )
            units.append(
                _html_unit(kind, text, heading_path, element, anchor, document.canonical_url)
            )

        if not units:
            issues.append(ExtractionIssue(ExtractionFailureReason.EMPTY_DOCUMENT))

        meta_catalog = _html_meta_values(soup, ("catalog", "catalog-year", "catalog_year"))
        meta_campus = _html_meta_values(soup, ("campus",))
        campus_labels = _unique(
            (*meta_campus, *(label for unit in units for label in unit.campus_labels))
        )
        catalog_context = _unique(
            (*meta_catalog, *(label for unit in units for label in unit.catalog_context))
        )
        links = _unique_links(
            (
                *(link for unit in units for link in unit.links),
                *_html_links(root, document.canonical_url, None),
            )
        )
        unique_issues = _unique_issues(issues)
        return ExtractedDocument(
            canonical_url=document.canonical_url,
            title=title,
            media_type=document.media_type,
            parser_version=PARSER_VERSION,
            units=tuple(units),
            tables=tuple(tables),
            links=links,
            campus_labels=campus_labels,
            catalog_context=catalog_context,
            issues=unique_issues,
            complete=not unique_issues,
        )

    def _extract_pdf(self, document: DiscoveredDocument) -> ExtractedDocument:
        issues: list[ExtractionIssue] = []
        try:
            reader = PdfReader(BytesIO(document.content), strict=False)
            if reader.is_encrypted and reader.decrypt("") == 0:
                return _failed_pdf(document, ExtractionFailureReason.ENCRYPTED_PDF)
        except (PdfReadError, OSError, ValueError, TypeError):
            return _failed_pdf(document, ExtractionFailureReason.UNREADABLE_PDF)

        title = _pdf_title(reader) or document.title
        try:
            pages = tuple(reader.pages)
        except (AttributeError, KeyError, TypeError, ValueError, PdfReadError):
            return _failed_pdf(document, ExtractionFailureReason.UNREADABLE_PDF)
        page_lines: list[list[str]] = []
        page_layout_lines: list[list[str]] = []
        page_links: list[tuple[ExtractedLink, ...]] = []
        for page_number, page in enumerate(pages, start=1):
            try:
                plain_text = page.extract_text() or ""
                layout_text = page.extract_text(extraction_mode="layout") or plain_text
                page_lines.append(_nonempty_lines(plain_text))
                page_layout_lines.append(layout_text.splitlines())
                page_links.append(_pdf_links(page, document.canonical_url, page_number))
            except (AttributeError, KeyError, TypeError, ValueError, PdfReadError):
                page_lines.append([])
                page_layout_lines.append([])
                page_links.append(())
                issues.append(
                    ExtractionIssue(
                        ExtractionFailureReason.PDF_PAGE_UNREADABLE,
                        page=page_number,
                        anchor=f"page={page_number}",
                    )
                )

        repeated_margins = _repeated_pdf_margins(page_lines)
        units: list[ExtractedUnit] = []
        tables: list[ExtractedTable] = []
        for page_number, (plain_lines, layout_lines, links) in enumerate(
            zip(page_lines, page_layout_lines, page_links, strict=True),
            start=1,
        ):
            filtered_lines = [line for line in plain_lines if _fold(line) not in repeated_margins]
            pdf_tables, table_line_text = _extract_pdf_tables(
                layout_lines,
                page_number,
            )
            tables.extend(pdf_tables)
            units.extend(_pdf_table_units(pdf_tables, len(tables) - len(pdf_tables)))
            filtered_lines = [line for line in filtered_lines if _fold(line) not in table_line_text]
            units.extend(_pdf_text_units(filtered_lines, page_number, links))

        if not units:
            issues.append(ExtractionIssue(ExtractionFailureReason.EMPTY_DOCUMENT))

        campus_labels = _unique(label for unit in units for label in unit.campus_labels)
        catalog_context = _unique(label for unit in units for label in unit.catalog_context)
        links = _unique_links(link for unit in units for link in unit.links)
        unique_issues = _unique_issues(issues)
        return ExtractedDocument(
            canonical_url=document.canonical_url,
            title=title,
            media_type=document.media_type,
            parser_version=PARSER_VERSION,
            units=tuple(units),
            tables=tuple(tables),
            links=links,
            campus_labels=campus_labels,
            catalog_context=catalog_context,
            issues=unique_issues,
            complete=not unique_issues,
        )


def _failed_pdf(
    document: DiscoveredDocument,
    reason: ExtractionFailureReason,
) -> ExtractedDocument:
    issue = ExtractionIssue(reason)
    return ExtractedDocument(
        canonical_url=document.canonical_url,
        title=document.title,
        media_type=document.media_type,
        parser_version=PARSER_VERSION,
        units=(),
        tables=(),
        links=(),
        campus_labels=(),
        catalog_context=(),
        issues=(issue,),
        complete=False,
    )


def _html_title(soup: BeautifulSoup) -> str | None:
    if soup.title is None:
        return None
    return _element_text(soup.title) or None


def _html_unit(
    kind: ExtractedUnitKind,
    text: str,
    heading_path: tuple[str, ...],
    element: Tag,
    anchor: str | None,
    base_url: str | None = None,
    *,
    direct_links: bool = False,
) -> ExtractedUnit:
    scope_text = " ".join((*heading_path, text, *_attribute_scope_values(element)))
    return ExtractedUnit(
        kind=kind,
        text=text,
        heading_path=heading_path,
        page=None,
        anchor=anchor,
        links=(
            _html_links(element, base_url, anchor, direct_only=direct_links) if base_url else ()
        ),
        campus_labels=_campus_labels(scope_text),
        catalog_context=_catalog_context(scope_text),
    )


def _extract_html_table(
    table_tag: Tag,
    base_url: str,
    heading_path: tuple[str, ...],
    table_index: int,
) -> tuple[ExtractedTable, tuple[ExtractedUnit, ...], tuple[ExtractionIssue, ...]]:
    anchor = _nearest_anchor(table_tag)
    caption_tag = table_tag.find("caption", recursive=False)
    caption = _element_text(caption_tag) if isinstance(caption_tag, Tag) else None
    caption = caption or None
    rows: list[ExtractedTableRow] = []
    units: list[ExtractedUnit] = []
    issues: list[ExtractionIssue] = []
    malformed = False
    widths: list[int] = []
    has_rowspan = False
    has_header = False

    for row_tag in table_tag.find_all("tr"):
        if row_tag.find_parent("table") is not table_tag:
            continue
        section = _table_section(row_tag)
        row_anchor = _nearest_anchor(row_tag) or anchor
        cells: list[ExtractedTableCell] = []
        width = 0
        for cell_tag in row_tag.find_all(("th", "td"), recursive=False):
            text = _element_text(cell_tag)
            colspan, colspan_valid = _span_value(cell_tag.get("colspan"))
            rowspan, rowspan_valid = _span_value(cell_tag.get("rowspan"))
            malformed = malformed or not colspan_valid or not rowspan_valid
            has_rowspan = has_rowspan or rowspan > 1
            is_header = cell_tag.name == "th"
            has_header = has_header or is_header
            scope_value = cell_tag.get("scope")
            scope = _clean_text(scope_value) if isinstance(scope_value, str) else None
            links = _html_links(cell_tag, base_url, row_anchor)
            cells.append(
                ExtractedTableCell(
                    text=text,
                    is_header=is_header,
                    scope=scope,
                    colspan=colspan,
                    rowspan=rowspan,
                    links=links,
                )
            )
            width += colspan
        if not cells:
            malformed = True
            continue
        widths.append(width)
        row = ExtractedTableRow(cells=tuple(cells), section=section, anchor=row_anchor)
        row_index = len(rows)
        rows.append(row)
        scope_text = " ".join((*heading_path, caption or "", row.text))
        units.append(
            ExtractedUnit(
                kind=(
                    ExtractedUnitKind.FOOTNOTE
                    if section is TableSection.FOOT
                    else ExtractedUnitKind.TABLE_ROW
                ),
                text=row.text,
                heading_path=heading_path,
                page=None,
                anchor=row_anchor,
                links=_unique_links(link for cell in cells for link in cell.links),
                campus_labels=_campus_labels(scope_text),
                catalog_context=_catalog_context(scope_text),
                table_index=table_index,
                table_row_index=row_index,
            )
        )

    if not rows or not has_header or (not has_rowspan and len(set(widths)) > 1):
        malformed = True
    if malformed:
        issues.append(ExtractionIssue(ExtractionFailureReason.MALFORMED_TABLE, anchor=anchor))

    footnotes = [row.text for row in rows if row.section is TableSection.FOOT]
    for sibling in table_tag.find_next_siblings(limit=3):
        if not isinstance(sibling, Tag) or not _is_footnote(sibling):
            break
        note = _element_text(sibling)
        if note:
            footnotes.append(note)

    extracted = ExtractedTable(
        caption=caption,
        heading_path=heading_path,
        page=None,
        anchor=anchor,
        rows=tuple(rows),
        footnotes=_unique(footnotes),
    )
    return extracted, tuple(units), tuple(issues)


def _table_section(row: Tag) -> TableSection:
    parent = row.parent
    if isinstance(parent, Tag) and parent.name == "thead":
        return TableSection.HEAD
    if isinstance(parent, Tag) and parent.name == "tfoot":
        return TableSection.FOOT
    return TableSection.BODY


def _span_value(value: object) -> tuple[int, bool]:
    if value is None:
        return 1, True
    try:
        parsed = int(str(value))
    except ValueError:
        return 1, False
    if not 1 <= parsed <= 100:
        return 1, False
    return parsed, True


def _expandable_issues(root: Tag, soup: BeautifulSoup) -> list[ExtractionIssue]:
    issues: list[ExtractionIssue] = []
    for control in root.select("[aria-controls]"):
        value = control.get("aria-controls")
        if not isinstance(value, str):
            continue
        for target_id in value.split():
            target = soup.find(id=target_id)
            if target is None or not _element_text(target):
                issues.append(
                    ExtractionIssue(
                        ExtractionFailureReason.MISSING_EXPANDABLE_CONTENT,
                        anchor=_nearest_anchor(control),
                    )
                )
    for details in root.find_all("details"):
        content = " ".join(
            _element_text(child)
            for child in details.find_all(recursive=False)
            if isinstance(child, Tag) and child.name != "summary"
        )
        if not content.strip():
            issues.append(
                ExtractionIssue(
                    ExtractionFailureReason.MISSING_EXPANDABLE_CONTENT,
                    anchor=_nearest_anchor(details),
                )
            )
    return issues


def _inside_excluded_container(element: Tag) -> bool:
    if element.name != "table" and element.find_parent("table") is not None:
        return True
    return element.name != "li" and element.find_parent("li") is not None


def _element_text(element: Tag | None, *, exclude_nested_lists: bool = False) -> str:
    if element is None:
        return ""
    if not exclude_nested_lists:
        return _clean_text(element.get_text(" ", strip=True))
    pieces: list[str] = []
    for descendant in element.descendants:
        if not isinstance(descendant, NavigableString):
            continue
        parents = descendant.parents
        if any(parent is not element and parent.name in ("ul", "ol") for parent in parents):
            continue
        pieces.append(str(descendant))
    return _clean_text(" ".join(pieces))


def _direct_container_text(element: Tag) -> str:
    pieces: list[str] = []
    for descendant in element.descendants:
        if not isinstance(descendant, NavigableString):
            continue
        current = descendant.parent
        blocked = False
        while isinstance(current, Tag) and current is not element:
            if current.name in _BLOCK_TAGS:
                blocked = True
                break
            current = current.parent
        if not blocked:
            pieces.append(str(descendant))
    return _clean_text(" ".join(pieces))


def _html_links(
    element: Tag,
    base_url: str | None,
    anchor: str | None,
    *,
    direct_only: bool = False,
) -> tuple[ExtractedLink, ...]:
    if base_url is None:
        return ()
    links: list[ExtractedLink] = []
    for link_tag in element.find_all("a", href=True):
        if link_tag.find_parent("a") is not None:
            continue
        if direct_only and _inside_nested_block(link_tag, element):
            continue
        href = link_tag.get("href")
        if not isinstance(href, str) or not href.strip():
            continue
        url = urljoin(base_url, href.strip())
        if urlsplit(url).scheme.lower() not in {"http", "https", "mailto", "tel"}:
            continue
        links.append(
            ExtractedLink(
                text=_element_text(link_tag),
                url=url,
                source_anchor=anchor or _nearest_anchor(link_tag),
            )
        )
    return _unique_links(links)


def _inside_nested_block(element: Tag, boundary: Tag) -> bool:
    current = element.parent
    while isinstance(current, Tag) and current is not boundary:
        if current.name in _BLOCK_TAGS:
            return True
        current = current.parent
    return False


def _nearest_anchor(element: Tag) -> str | None:
    current: Tag | None = element
    while current is not None:
        value = current.get("id")
        if isinstance(value, str) and value.strip():
            return value.strip()[:512]
        current = current.parent if isinstance(current.parent, Tag) else None
    return None


def _is_footnote(element: Tag) -> bool:
    if element.get("role") == "note":
        return True
    tokens: list[str] = []
    element_id = element.get("id")
    if isinstance(element_id, str):
        tokens.append(element_id)
    classes = element.get("class")
    if isinstance(classes, list):
        tokens.extend(str(item) for item in classes)
    return any(_FOOTNOTE_TOKEN_RE.search(token) for token in tokens)


def _attribute_scope_values(element: Tag) -> tuple[str, ...]:
    values: list[str] = []
    current: Tag | None = element
    while current is not None:
        for name in ("data-campus", "data-catalog", "data-catalog-year"):
            value = current.get(name)
            if isinstance(value, str) and value.strip():
                values.append(value)
        current = current.parent if isinstance(current.parent, Tag) else None
    return tuple(values)


def _html_meta_values(soup: BeautifulSoup, names: Sequence[str]) -> tuple[str, ...]:
    normalized_names = frozenset(item.casefold() for item in names)
    values: list[str] = []
    for tag in soup.find_all("meta"):
        name = tag.get("name") or tag.get("property")
        content = tag.get("content")
        if (
            isinstance(name, str)
            and name.casefold() in normalized_names
            and isinstance(content, str)
            and content.strip()
        ):
            values.append(_clean_text(content))
    return _unique(values)


def _pdf_title(reader: PdfReader) -> str | None:
    try:
        title = reader.metadata.title if reader.metadata is not None else None
    except (AttributeError, KeyError, TypeError, ValueError):
        return None
    return _clean_text(title) if isinstance(title, str) and title.strip() else None


def _pdf_links(page: object, base_url: str, page_number: int) -> tuple[ExtractedLink, ...]:
    links: list[ExtractedLink] = []
    try:
        annotations = page.get("/Annots") or ()  # type: ignore[attr-defined]
        for reference in annotations:
            annotation = reference.get_object()
            action = annotation.get("/A")
            uri = action.get("/URI") if action is not None else None
            destination = annotation.get("/Dest")
            if isinstance(uri, str) and uri.strip():
                url = urljoin(base_url, uri.strip())
            elif destination is not None:
                url = _replace_fragment(base_url, str(destination).lstrip("/"))
            else:
                continue
            links.append(
                ExtractedLink(
                    text="PDF link",
                    url=url,
                    page=page_number,
                    source_anchor=f"page={page_number}",
                )
            )
    except (AttributeError, KeyError, TypeError, ValueError):
        return ()
    return _unique_links(links)


def _replace_fragment(url: str, fragment: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, fragment))


def _nonempty_lines(text: str) -> list[str]:
    return [cleaned for line in text.splitlines() if (cleaned := _clean_text(line))]


def _repeated_pdf_margins(page_lines: Sequence[Sequence[str]]) -> frozenset[str]:
    if len(page_lines) < 2:
        return frozenset()
    counts: dict[str, int] = {}
    for lines in page_lines:
        candidates = {_fold(line) for line in (*lines[:2], *lines[-2:]) if line}
        for candidate in candidates:
            counts[candidate] = counts.get(candidate, 0) + 1
    return frozenset(value for value, count in counts.items() if count >= 2)


def _extract_pdf_tables(
    layout_lines: Sequence[str],
    page_number: int,
) -> tuple[tuple[ExtractedTable, ...], frozenset[str]]:
    groups: list[list[tuple[str, ...]]] = []
    current: list[tuple[str, ...]] = []
    for raw_line in layout_lines:
        stripped = raw_line.strip()
        parsed_cells = tuple(
            _clean_text(value) for value in _PDF_TABLE_SEPARATOR_RE.split(stripped)
        )
        parsed_cells = tuple(value for value in parsed_cells if value)
        if len(parsed_cells) >= 2:
            if current and len(parsed_cells) != len(current[0]):
                if len(current) >= 2:
                    groups.append(current)
                current = []
            current.append(parsed_cells)
        else:
            if len(current) >= 2:
                groups.append(current)
            current = []
    if len(current) >= 2:
        groups.append(current)

    tables: list[ExtractedTable] = []
    consumed: set[str] = set()
    for group in groups:
        rows: list[ExtractedTableRow] = []
        for row_number, values in enumerate(group):
            consumed.add(_fold(" ".join(values)))
            extracted_cells = tuple(
                ExtractedTableCell(
                    text=value,
                    is_header=row_number == 0,
                    scope="col" if row_number == 0 else None,
                    colspan=1,
                    rowspan=1,
                )
                for value in values
            )
            rows.append(
                ExtractedTableRow(
                    cells=extracted_cells,
                    section=TableSection.HEAD if row_number == 0 else TableSection.BODY,
                    anchor=f"page={page_number}",
                )
            )
        tables.append(
            ExtractedTable(
                caption=None,
                heading_path=(),
                page=page_number,
                anchor=f"page={page_number}",
                rows=tuple(rows),
                footnotes=(),
            )
        )
    return tuple(tables), frozenset(consumed)


def _pdf_table_units(
    tables: Sequence[ExtractedTable],
    starting_index: int,
) -> tuple[ExtractedUnit, ...]:
    units: list[ExtractedUnit] = []
    for offset, table in enumerate(tables):
        for row_index, row in enumerate(table.rows):
            scope_text = " ".join((*table.heading_path, table.caption or "", row.text))
            units.append(
                ExtractedUnit(
                    kind=ExtractedUnitKind.TABLE_ROW,
                    text=row.text,
                    heading_path=table.heading_path,
                    page=table.page,
                    anchor=table.anchor,
                    campus_labels=_campus_labels(scope_text),
                    catalog_context=_catalog_context(scope_text),
                    table_index=starting_index + offset,
                    table_row_index=row_index,
                )
            )
    return tuple(units)


def _pdf_text_units(
    lines: Sequence[str],
    page_number: int,
    links: tuple[ExtractedLink, ...],
) -> tuple[ExtractedUnit, ...]:
    units: list[ExtractedUnit] = []
    heading_path: tuple[str, ...] = ()
    paragraph: list[str] = []
    page_links = links

    def append_paragraph() -> None:
        nonlocal page_links
        if not paragraph:
            return
        text = _clean_text(" ".join(paragraph))
        kind = (
            ExtractedUnitKind.FOOTNOTE
            if _PDF_FOOTNOTE_RE.match(text)
            else ExtractedUnitKind.PARAGRAPH
        )
        scope_text = " ".join((*heading_path, text))
        units.append(
            ExtractedUnit(
                kind=kind,
                text=text,
                heading_path=heading_path,
                page=page_number,
                anchor=f"page={page_number}",
                links=page_links,
                campus_labels=_campus_labels(scope_text),
                catalog_context=_catalog_context(scope_text),
            )
        )
        page_links = ()
        paragraph.clear()

    for line in lines:
        if _looks_like_pdf_heading(line):
            append_paragraph()
            heading_path = (line,)
            units.append(
                ExtractedUnit(
                    kind=ExtractedUnitKind.HEADING,
                    text=line,
                    heading_path=heading_path,
                    page=page_number,
                    anchor=f"page={page_number}",
                    links=page_links,
                    campus_labels=_campus_labels(line),
                    catalog_context=_catalog_context(line),
                )
            )
            page_links = ()
        else:
            paragraph.append(line)
    append_paragraph()
    return tuple(units)


def _looks_like_pdf_heading(line: str) -> bool:
    if len(line) > 120 or len(line.split()) > 16 or line.endswith((".", "!", "?", ";", ":")):
        return False
    if line.startswith(("http://", "https://")):
        return False
    letters = [character for character in line if character.isalpha()]
    return bool(letters)


def _campus_labels(text: str) -> tuple[str, ...]:
    labels: list[str] = []
    for match in _CAMPUS_RE.finditer(text):
        canonical = match.group(1).capitalize()
        if canonical not in labels:
            labels.append(canonical)
    return tuple(labels)


def _catalog_context(text: str) -> tuple[str, ...]:
    values: list[str] = []
    for pattern in _CATALOG_PATTERNS:
        values.extend(_clean_text(match.group(0)) for match in pattern.finditer(text))
    unique_values = _unique(values)
    return tuple(
        value
        for value in unique_values
        if not any(
            value.casefold() != candidate.casefold() and value.casefold() in candidate.casefold()
            for candidate in unique_values
        )
    )


def _clean_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.replace("\u00a0", " ").split())


def _fold(value: str) -> str:
    return _clean_text(value).casefold()


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _unique_links(values: Iterable[ExtractedLink]) -> tuple[ExtractedLink, ...]:
    return tuple(dict.fromkeys(values))


def _unique_issues(values: Iterable[ExtractionIssue]) -> tuple[ExtractionIssue, ...]:
    return tuple(dict.fromkeys(values))


__all__ = [
    "PARSER_VERSION",
    "ExtractedDocument",
    "ExtractedLink",
    "ExtractedTable",
    "ExtractedTableCell",
    "ExtractedTableRow",
    "ExtractedUnit",
    "ExtractedUnitKind",
    "ExtractionFailureReason",
    "ExtractionInputError",
    "ExtractionIssue",
    "StructuredExtractor",
    "TableSection",
]
