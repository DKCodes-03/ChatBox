from ingestion.chunk_content import chunk_content
from ingestion.extract_content import ExtractedContent, ExtractedElement


def test_chunk_content_preserves_section_structure_and_table_rows() -> None:
    extracted = ExtractedContent(
        title="Registration",
        update_metadata=(),
        elements=(
            ExtractedElement(
                kind="heading",
                text="Registration",
                heading_level=1,
                page_ref="2",
            ),
            ExtractedElement(
                kind="paragraph",
                text="Students must complete the prerequisites before registering for the term.",
            ),
            ExtractedElement(
                kind="list_item",
                text="Meet with an advisor for registration clearance.",
                ordered=True,
                list_depth=1,
            ),
            ExtractedElement(
                kind="table_row",
                text="Deadline | August 1",
                table_index=0,
                row_index=0,
                cells=("Deadline", "August 1"),
                is_header=False,
            ),
        ),
        links=(),
    )

    chunks = chunk_content(extracted, max_chars=500)

    assert len(chunks) == 1
    assert chunks[0].heading == "Registration"
    assert chunks[0].page_ref == "2"
    assert "prerequisites" in chunks[0].content
    assert "Meet with an advisor" in chunks[0].content
    assert "Deadline | August 1" in chunks[0].content
    assert chunks[0].content.splitlines()[0] != "Registration"


def test_chunk_content_flushes_when_section_changes() -> None:
    extracted = ExtractedContent(
        title="Student services",
        update_metadata=(),
        elements=(
            ExtractedElement(kind="heading", text="Admissions", heading_level=2),
            ExtractedElement(kind="paragraph", text="Apply online before the deadline."),
            ExtractedElement(kind="heading", text="Parking", heading_level=2),
            ExtractedElement(kind="paragraph", text="Permit fees are due each semester."),
        ),
        links=(),
    )

    chunks = chunk_content(extracted, max_chars=300)

    assert len(chunks) == 2
    assert [chunk.heading for chunk in chunks] == ["Admissions", "Parking"]
