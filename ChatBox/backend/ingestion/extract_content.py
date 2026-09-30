from __future__ import annotations

import re
from dataclasses import dataclass
from io import BytesIO
from typing import Literal
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, Tag
from bs4.element import NavigableString
from pypdf import PdfReader

ElementKind = Literal["heading", "paragraph", "list_item", "table_caption", "table_row"]

_REMOVED_TAGS = frozenset(
    {"nav", "footer", "script", "style", "noscript", "template", "svg", "canvas", "iframe"}
)
_DECORATIVE_TOKENS = frozenset(
    {
        "advert",
        "advertisement",
        "breadcrumb",
        "breadcrumbs",
        "cookie",
        "decoration",
        "decorative",
        "icon",
        "menu",
        "navigation",
        "pagination",
        "share",
        "social",
    }
)
_UPDATE_KEYS = frozenset(
    {
        "article:modified_time",
        "date",
        "date-modified",
        "datemodified",
        "dc.date",
        "dc.date.modified",
        "dcterms.date",
        "dcterms.modified",
        "last-modified",
        "lastmodified",
        "modified",
        "modified_time",
        "og:updated_time",
        "publication_date",
        "published_time",
    }
)
_BLOCK_TAGS = ["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "table"]


@dataclass(frozen=True)
class ExtractedLink:
    text: str
    url: str


@dataclass(frozen=True)
class ExtractionIssue:
    code: str
    message: str
    page_ref: str | None = None


@dataclass(frozen=True)
class ExtractedElement:
    kind: ElementKind
    text: str
    heading_level: int | None = None
    ordered: bool | None = None
    list_depth: int | None = None
    table_index: int | None = None
    row_index: int | None = None
    cells: tuple[str, ...] = ()
    is_header: bool = False
    page_ref: str | None = None
    links: tuple[ExtractedLink, ...] = ()


@dataclass(frozen=True)
class ExtractedContent:
    title: str | None
    update_metadata: tuple[tuple[str, str], ...]
    elements: tuple[ExtractedElement, ...]
    links: tuple[ExtractedLink, ...]
    issues: tuple[ExtractionIssue, ...] = ()


def _clean_text(value: str) -> str:
    return " ".join(value.split())


def _inside_nested_list_item(node: Tag | NavigableString, container: Tag) -> bool:
    parent = node.parent
    while parent is not None and parent is not container:
        if isinstance(parent, Tag) and parent.name == "li":
            return True
        parent = parent.parent
    return False


def _node_text(node: Tag, *, exclude_nested_list_items: bool = False) -> str:
    parts: list[str] = []
    for descendant in node.descendants:
        if isinstance(descendant, NavigableString):
            if exclude_nested_list_items and _inside_nested_list_item(descendant, node):
                continue
            parts.append(str(descendant))
        elif isinstance(descendant, Tag) and descendant.name == "br":
            parts.append(" ")
    return _clean_text("".join(parts))


def _link_from_anchor(anchor: Tag, base_url: str) -> ExtractedLink | None:
    href = anchor.get("href")
    if not isinstance(href, str) or not href.strip():
        return None

    url = urljoin(base_url, href.strip())
    if urlsplit(url).scheme.lower() not in {"http", "https", "mailto", "tel"}:
        return None
    return ExtractedLink(text=_node_text(anchor), url=url)


def _links_in(
    node: Tag, base_url: str, *, exclude_nested_list_items: bool = False
) -> tuple[ExtractedLink, ...]:
    links: list[ExtractedLink] = []
    for anchor in node.find_all("a", href=True):
        if exclude_nested_list_items and _inside_nested_list_item(anchor, node):
            continue
        link = _link_from_anchor(anchor, base_url)
        if link is not None:
            links.append(link)
    return tuple(links)


def _remove_navigation_and_decoration(soup: BeautifulSoup) -> None:
    for node in list(soup.find_all(True)):
        if node.parent is None:
            continue

        class_attribute = node.get("class")
        if isinstance(class_attribute, str):
            class_values = [class_attribute]
        elif class_attribute is None:
            class_values = []
        else:
            class_values = [str(value) for value in class_attribute]
        identifier = node.get("id")
        if isinstance(identifier, str):
            class_values.append(identifier)
        class_tokens = {
            token for value in class_values for token in re.findall(r"[a-z0-9]+", value.lower())
        }
        role = str(node.get("role", "")).lower()
        aria_hidden = str(node.get("aria-hidden", "")).lower()

        if (
            node.name in _REMOVED_TAGS
            or class_tokens.intersection(_DECORATIVE_TOKENS)
            or role in {"none", "presentation"}
            or aria_hidden == "true"
        ):
            node.decompose()


def _extract_update_metadata(soup: BeautifulSoup) -> tuple[tuple[str, str], ...]:
    metadata: list[tuple[str, str]] = []

    for meta in soup.find_all("meta"):
        key = str(meta.get("itemprop") or meta.get("property") or meta.get("name") or "")
        normalized_key = key.strip().lower()
        value = meta.get("content")
        if normalized_key in _UPDATE_KEYS and isinstance(value, str) and value.strip():
            metadata.append((normalized_key, _clean_text(value)))

    for time_node in soup.find_all("time"):
        itemprop = str(time_node.get("itemprop", "")).strip().lower()
        if itemprop not in {"datemodified", "datepublished", "datecreated"}:
            continue
        value = time_node.get("datetime") or _node_text(time_node)
        if isinstance(value, str) and value.strip():
            metadata.append((itemprop, _clean_text(value)))

    return tuple(metadata)


def _extract_page_ref(value: str) -> str | None:
    match = re.search(r"(?i)\bpage\s*(?:#)?\s*([0-9]+|[ivxlcdm]+)\b", value)
    if match is None:
        return None
    return match.group(1)


def _decode_document_text(content: bytes, content_type: str) -> str:
    normalized_type = content_type.split(";", 1)[0].strip().lower()
    if normalized_type in {"text/html", "text/plain"}:
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError:
            return content.decode("utf-8", errors="replace")

    for encoding in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def _text_to_elements(
    text: str, *, source_url: str, page_ref: str | None = None
) -> tuple[ExtractedElement, ...]:
    paragraphs: list[str] = []
    for block in re.split(r"\n\s*\n+", text):
        cleaned = _clean_text(block)
        if not cleaned:
            continue
        paragraphs.append(cleaned)

    elements: list[ExtractedElement] = []
    for paragraph in paragraphs:
        element_page_ref = _extract_page_ref(paragraph) or page_ref
        if paragraph.count(" ") <= 2 and len(paragraph) < 60:
            elements.append(
                ExtractedElement(
                    kind="heading",
                    text=paragraph,
                    heading_level=1,
                    page_ref=element_page_ref,
                    links=(),
                )
            )
            continue
        elements.append(
            ExtractedElement(
                kind="paragraph",
                text=paragraph,
                page_ref=element_page_ref,
                links=(),
            )
        )
    return tuple(elements)


def extract_document(
    content: bytes,
    source_url: str,
    *,
    content_type: str = "text/plain",
) -> ExtractedContent:
    """Extract plain-text and PDF-style document content while reporting malformed sources."""
    normalized_type = content_type.split(";", 1)[0].strip().lower()

    if normalized_type == "text/html":
        return extract_html(content, source_url)

    if normalized_type == "application/pdf":
        try:
            reader = PdfReader(BytesIO(content), strict=False)
            elements = tuple(
                element
                for page_number, page in enumerate(reader.pages, start=1)
                for element in _text_to_elements(
                    page.extract_text() or "",
                    source_url=source_url,
                    page_ref=str(page_number),
                )
            )
        except Exception as error:  # noqa: BLE001
            return ExtractedContent(
                title=None,
                update_metadata=(),
                elements=(),
                links=(),
                issues=(
                    ExtractionIssue(
                        code="malformed_document",
                        message=f"PDF text extraction failed: {error}",
                    ),
                ),
            )

        title = next(
            (element.text for element in elements if element.kind == "heading"),
            None,
        )
        issues = () if elements else (
            ExtractionIssue(
                code="malformed_document",
                message="The PDF contains no extractable text.",
            ),
        )
        return ExtractedContent(
            title=title,
            update_metadata=(),
            elements=elements,
            links=(),
            issues=issues,
        )

    text = _decode_document_text(content, normalized_type)
    elements = _text_to_elements(text, source_url=source_url)

    title = None
    for element in elements:
        if element.kind == "heading":
            title = element.text
            break

    return ExtractedContent(
        title=title,
        update_metadata=(),
        elements=elements,
        links=(),
        issues=(),
    )


def extract_html(content: bytes, source_url: str) -> ExtractedContent:
    """Extract answerable HTML structure, direct links, and available update dates."""
    soup = BeautifulSoup(content, "html.parser")
    update_metadata = _extract_update_metadata(soup)
    title_node = soup.title
    title = _node_text(title_node) if isinstance(title_node, Tag) else None

    _remove_navigation_and_decoration(soup)

    root = soup.find("main")
    if root is None:
        root = soup.select_one('[role="main"]')
    if root is None:
        root = soup.find("article")
    if root is None:
        root = soup.body or soup

    if not title:
        heading = root.find("h1")
        title = _node_text(heading) if isinstance(heading, Tag) else None

    elements: list[ExtractedElement] = []
    table_index = 0

    for node in root.find_all(_BLOCK_TAGS):
        if node.name == "table":
            current_table_index = table_index
            table_index += 1
            caption = node.find("caption", recursive=False)
            if isinstance(caption, Tag):
                caption_text = _node_text(caption)
                if caption_text:
                    elements.append(
                        ExtractedElement(
                            kind="table_caption",
                            text=caption_text,
                            table_index=current_table_index,
                            links=_links_in(caption, source_url),
                        )
                    )

            row_index = 0
            for row in node.find_all("tr"):
                if row.find_parent("table") is not node:
                    continue
                cells = tuple(
                    _node_text(cell) for cell in row.find_all(["th", "td"], recursive=False)
                )
                if not cells:
                    continue
                elements.append(
                    ExtractedElement(
                        kind="table_row",
                        text=" | ".join(cells),
                        table_index=current_table_index,
                        row_index=row_index,
                        cells=cells,
                        is_header=all(
                            cell.name == "th"
                            for cell in row.find_all(["th", "td"], recursive=False)
                        ),
                        links=_links_in(row, source_url),
                    )
                )
                row_index += 1
            continue

        if node.find_parent("table") is not None:
            continue

        if node.name.startswith("h") and len(node.name) == 2 and node.name[1].isdigit():
            text = _node_text(node)
            if text:
                elements.append(
                    ExtractedElement(
                        kind="heading",
                        text=text,
                        heading_level=int(node.name[1]),
                        links=_links_in(node, source_url),
                    )
                )
        elif node.name == "p":
            text = _node_text(node)
            if text:
                elements.append(
                    ExtractedElement(
                        kind="paragraph",
                        text=text,
                        links=_links_in(node, source_url),
                    )
                )
        elif node.name == "li":
            text = _node_text(node, exclude_nested_list_items=True)
            if text:
                list_parent = node.find_parent(["ol", "ul"])
                list_depth = len(node.find_parents(["ol", "ul"]))
                elements.append(
                    ExtractedElement(
                        kind="list_item",
                        text=text,
                        ordered=isinstance(list_parent, Tag) and list_parent.name == "ol",
                        list_depth=list_depth,
                        links=_links_in(node, source_url, exclude_nested_list_items=True),
                    )
                )

    links: list[ExtractedLink] = []
    seen_links: set[tuple[str, str]] = set()
    for anchor in root.find_all("a", href=True):
        link = _link_from_anchor(anchor, source_url)
        if link is not None and (link.text, link.url) not in seen_links:
            seen_links.add((link.text, link.url))
            links.append(link)

    return ExtractedContent(
        title=title or None,
        update_metadata=update_metadata,
        elements=tuple(elements),
        links=tuple(links),
        issues=(),
    )
