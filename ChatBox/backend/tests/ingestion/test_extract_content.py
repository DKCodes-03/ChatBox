from ingestion.extract_content import extract_document, extract_html


def test_extract_document_handles_plain_text_and_reports_malformed_content() -> None:
    text = b"Academic Policies\n\nStudents must complete 120 credits.\nPage 2\n"

    extracted = extract_document(text, "https://www.pnw.edu/policies", content_type="text/plain")

    assert [
        element.kind for element in extracted.elements if element.kind in {"heading", "paragraph"}
    ] == [
        "heading",
        "paragraph",
    ]
    assert extracted.issues == ()

    malformed = extract_document(
        b"%PDF-1.4\n<<broken>>", "https://www.pnw.edu/bad.pdf", content_type="application/pdf"
    )
    assert malformed.issues
    assert malformed.issues[0].code == "malformed_document"


def test_extract_html_preserves_structure_metadata_and_links() -> None:
    html = b"""
    <!doctype html>
    <html>
      <head>
        <title>Registration dates</title>
        <meta property="article:modified_time" content="2026-08-15T12:00:00Z">
      </head>
      <body>
        <nav><a href="/menu">Main menu</a></nav>
        <main>
          <h1>Registration</h1>
          <div class="decorative"><p>Hidden decoration</p></div>
          <h2>Fall term</h2>
          <p>Register <a href="../forms/register.pdf">using the official form</a>.</p>
          <ol><li>Meet with an advisor<ul><li>Bring your plan</li></ul></li></ol>
          <table>
            <caption>Important dates</caption>
            <thead><tr><th>Event</th><th>Date</th></tr></thead>
            <tbody><tr><td>Registration opens</td><td>August 1</td></tr></tbody>
          </table>
        </main>
        <footer><p>Footer text</p></footer>
      </body>
    </html>
    """

    extracted = extract_html(html, "https://www.pnw.edu/registrar/registration/")

    assert extracted.title == "Registration dates"
    assert extracted.update_metadata == (("article:modified_time", "2026-08-15T12:00:00Z"),)
    assert not any(
        excluded in element.text
        for element in extracted.elements
        for excluded in ("Main menu", "Hidden decoration", "Footer text")
    )

    headings = [element for element in extracted.elements if element.kind == "heading"]
    assert [(heading.text, heading.heading_level) for heading in headings] == [
        ("Registration", 1),
        ("Fall term", 2),
    ]

    list_items = [element for element in extracted.elements if element.kind == "list_item"]
    assert [(item.text, item.ordered, item.list_depth) for item in list_items] == [
        ("Meet with an advisor", True, 1),
        ("Bring your plan", False, 2),
    ]

    rows = [element for element in extracted.elements if element.kind == "table_row"]
    assert [(row.cells, row.is_header) for row in rows] == [
        (("Event", "Date"), True),
        (("Registration opens", "August 1"), False),
    ]
    assert rows[0].table_index == rows[1].table_index == 0
    assert [(link.text, link.url) for link in extracted.links] == [
        (
            "using the official form",
            "https://www.pnw.edu/registrar/forms/register.pdf",
        )
    ]


def test_extract_html_reads_date_from_time_metadata_and_falls_back_to_heading() -> None:
    html = b"""
    <article>
      <h1>Student services</h1>
      <time itemprop="dateModified" datetime="2026-09-01">September 1</time>
      <p>Contact the office.</p>
    </article>
    """

    extracted = extract_html(html, "https://www.pnw.edu/services")

    assert extracted.title == "Student services"
    assert extracted.update_metadata == (("datemodified", "2026-09-01"),)
    assert [element.text for element in extracted.elements if element.kind == "paragraph"] == [
        "Contact the office."
    ]


def test_extract_html_keeps_conflicting_metadata_and_preserves_table_rows() -> None:
    html = b"""
    <html>
      <head>
        <title>Academic calendar</title>
        <meta property="article:modified_time" content="2026-08-15T12:00:00Z">
        <meta name="last-modified" content="2026-09-01T12:00:00Z">
      </head>
      <body>
        <nav><a href="/home">Home</a></nav>
        <main>
          <h1>Academic calendar</h1>
          <p>Registration opens for continuing students.</p>
          <table>
            <caption>Fall deadlines</caption>
            <tr><th>Item</th><th>Due date</th></tr>
            <tr><td>Priority registration</td><td>August 1</td></tr>
          </table>
        </main>
      </body>
    </html>
    """

    extracted = extract_html(html, "https://www.pnw.edu/registration")

    assert extracted.title == "Academic calendar"
    assert {key for key, _ in extracted.update_metadata} == {
        "article:modified_time",
        "last-modified",
    }
    assert {(key, value) for key, value in extracted.update_metadata} == {
        ("article:modified_time", "2026-08-15T12:00:00Z"),
        ("last-modified", "2026-09-01T12:00:00Z"),
    }
    rows = [element for element in extracted.elements if element.kind == "table_row"]
    assert [(row.cells, row.is_header) for row in rows] == [
        (("Item", "Due date"), True),
        (("Priority registration", "August 1"), False),
    ]


def test_extract_document_handles_valid_pdf_and_reports_malformed_content() -> None:
    valid = (
        b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>\nendobj\n"
        b"4 0 obj\n<< /Length 55 >>\nstream\nBT\n/F1 12 Tf\n72 720 Td\n(Academic calendar) Tj\nET\nendstream\nendobj\n"
        b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>\nendobj\n"
        b"xref\n0 6\n0000000000 65535 f \n0000000010 00000 n \n0000000065 00000 n \n0000000123 00000 n \n0000000456 00000 n \n0000000500 00000 n \ntrailer\n<< /Root 1 0 R /Size 6 >>\nstartxref\n520\n%%EOF"
    )
    extracted = extract_document(
        valid, "https://www.pnw.edu/calendar.pdf", content_type="application/pdf"
    )

    assert extracted.issues == ()
    assert any("Academic calendar" in element.text for element in extracted.elements)
    assert all(element.page_ref == "1" for element in extracted.elements)

    malformed = extract_document(
        b"%PDF-1.4\n<<broken>>", "https://www.pnw.edu/bad.pdf", content_type="application/pdf"
    )
    assert malformed.issues
    assert malformed.issues[0].code == "malformed_document"
