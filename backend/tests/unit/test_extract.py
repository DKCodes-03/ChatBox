"""Structure-preservation and safe-failure checks for source extraction."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from app.ingestion.discovery import DiscoveredDocument
from app.ingestion.extract import (
    ExtractedUnitKind,
    ExtractionFailureReason,
    StructuredExtractor,
    TableSection,
)
from app.models.enums import MediaType
from pypdf import PdfReader, PdfWriter
from pypdf.annotations import Link

FIXTURES = Path(__file__).parents[1] / "fixtures" / "corpus"


def _document(
    content: bytes,
    media_type: MediaType = MediaType.HTML,
    *,
    url: str = "https://www.pnw.edu/policy/",
    title: str = "Fallback title",
) -> DiscoveredDocument:
    return DiscoveredDocument(
        requested_url=url,
        canonical_url=url,
        title=title,
        topics=("registration",),
        media_type=media_type,
        content=content,
        depth=0,
        parent_url=None,
        redirect_chain=(),
    )


def test_html_extraction_preserves_source_structure_and_scope() -> None:
    html = b"""
        <!doctype html>
        <html>
          <head>
            <title>Registration dates</title>
            <meta name="campus" content="Hammond">
          </head>
          <body>
            <nav>Repeated navigation must not become evidence.</nav>
            <main>
              <h1>Registration</h1>
              <section id="deadlines" data-campus="Hammond"
                       data-catalog-year="2030-2031 graduate catalog">
                <h2>Add and drop dates</h2>
                <p>Read the <a href="/registrar/drop/#form">drop instructions</a>.</p>
                <details>
                  <summary>Additional condition</summary>
                  <p id="expanded-condition">Advisor approval is required.</p>
                </details>
                <div id="boxed-condition">A boxed condition remains evidence.</div>
                <table id="refund-table">
                  <caption>Hammond refund schedule</caption>
                  <thead>
                    <tr><th scope="col">Event</th><th scope="col">Deadline</th></tr>
                  </thead>
                  <tbody>
                    <tr id="refund-row"><th scope="row">Drop</th><td>September 6</td></tr>
                  </tbody>
                  <tfoot>
                    <tr><td colspan="2">Dates use Central Time.</td></tr>
                  </tfoot>
                </table>
                <p class="table-note">The table applies only to the stated catalog.</p>
              </section>
            </main>
          </body>
        </html>
    """

    extracted = StructuredExtractor().extract(_document(html))

    assert extracted.complete is True
    assert extracted.title == "Registration dates"
    assert "Repeated navigation" not in extracted.text
    assert extracted.campus_labels == ("Hammond",)
    assert "2030-2031 graduate catalog" in extracted.catalog_context
    assert extracted.links[0].text == "drop instructions"
    assert extracted.links[0].url == "https://www.pnw.edu/registrar/drop/#form"
    assert extracted.links[0].source_anchor == "deadlines"

    expanded = next(unit for unit in extracted.units if unit.anchor == "expanded-condition")
    assert expanded.heading_path == ("Registration", "Add and drop dates")
    assert expanded.catalog_context == ("2030-2031 graduate catalog",)
    assert "A boxed condition remains evidence." in extracted.text

    assert len(extracted.tables) == 1
    table = extracted.tables[0]
    assert table.caption == "Hammond refund schedule"
    assert table.anchor == "refund-table"
    assert table.rows[0].section is TableSection.HEAD
    assert table.rows[0].cells[0].is_header is True
    assert table.rows[0].cells[0].scope == "col"
    assert table.rows[1].anchor == "refund-row"
    assert table.rows[2].cells[0].colspan == 2
    assert table.footnotes == (
        "Dates use Central Time.",
        "The table applies only to the stated catalog.",
    )
    assert any(unit.kind is ExtractedUnitKind.FOOTNOTE for unit in extracted.units)


def test_present_expandable_content_is_extracted_even_when_hidden() -> None:
    html = b"""
        <main>
          <h1>Policy</h1>
          <button aria-controls="answer" aria-expanded="false">Show answer</button>
          <div id="answer" hidden><p>The complete condition remains available.</p></div>
        </main>
    """

    extracted = StructuredExtractor().extract(_document(html))

    assert extracted.complete is True
    assert "The complete condition remains available." in extracted.text


def test_missing_expandable_content_is_reason_coded_as_incomplete() -> None:
    html = b"""
        <main>
          <h1>Policy</h1>
          <button aria-controls="missing-panel" aria-expanded="false">Show answer</button>
        </main>
    """

    extracted = StructuredExtractor().extract(_document(html))

    assert extracted.complete is False
    assert extracted.issues[0].reason is ExtractionFailureReason.MISSING_EXPANDABLE_CONTENT


def test_malformed_table_is_preserved_and_reason_coded() -> None:
    html = b"""
        <main>
          <h1>Schedule</h1>
          <table id="broken">
            <tr><td>Event</td><td>Date</td></tr>
            <tr><td>Drop</td></tr>
          </table>
        </main>
    """

    extracted = StructuredExtractor().extract(_document(html))

    assert extracted.complete is False
    assert len(extracted.tables[0].rows) == 2
    assert any(
        issue.reason is ExtractionFailureReason.MALFORMED_TABLE for issue in extracted.issues
    )


def test_pdf_extraction_preserves_pages_headings_and_page_anchors() -> None:
    content = (FIXTURES / "us1" / "replacement-form.pdf").read_bytes()

    extracted = StructuredExtractor().extract(
        _document(
            content,
            MediaType.PDF,
            url="https://www.pnw.edu/__test__/blue-lantern/replacement-form.pdf",
            title="Synthetic replacement instructions",
        )
    )

    assert extracted.complete is True
    assert {unit.page for unit in extracted.units} == {1, 2}
    assert {unit.anchor for unit in extracted.units} == {"page=1", "page=2"}
    assert any(
        unit.kind is ExtractedUnitKind.HEADING and unit.text == "Steps 3 and 4"
        for unit in extracted.units
    )
    assert "Step 4. Attach the sample image" in extracted.text
    assert "TEST-COMPLETE" in extracted.text
    assert all(unit.text != "SYNTHETIC TEST FIXTURE - NOT PNW POLICY" for unit in extracted.units)


def test_unreadable_pdf_returns_a_quarantinable_failure() -> None:
    extracted = StructuredExtractor().extract(_document(b"not a pdf", MediaType.PDF))

    assert extracted.complete is False
    assert extracted.units == ()
    assert extracted.issues[0].reason is ExtractionFailureReason.UNREADABLE_PDF


def test_pdf_link_annotation_retains_target_and_page_reference() -> None:
    original = (FIXTURES / "us1" / "replacement-form.pdf").read_bytes()
    writer = PdfWriter()
    writer.append_pages_from_reader(PdfReader(BytesIO(original)))
    writer.add_annotation(
        page_number=0,
        annotation=Link(
            rect=(72, 560, 360, 580),
            url="https://www.pnw.edu/forms/replacement/#submit",
        ),
    )
    updated = BytesIO()
    writer.write(updated)

    extracted = StructuredExtractor().extract(_document(updated.getvalue(), MediaType.PDF))

    link = next(item for item in extracted.links if item.text == "PDF link")
    assert link.url == "https://www.pnw.edu/forms/replacement/#submit"
    assert link.page == 1
    assert link.source_anchor == "page=1"
