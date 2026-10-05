"""Semantic boundary, parent-ID, and exact wordpiece checks for ingestion chunks."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from app.ingestion.chunking import (
    ChunkingContext,
    ChunkingFailureReason,
    ChunkingInputError,
    ChunkingResult,
    SemanticChunker,
    SemanticUnitTooLargeError,
    embedding_text,
)
from app.ingestion.discovery import DiscoveredDocument
from app.ingestion.extract import ExtractedDocument, StructuredExtractor
from app.models.enums import ExtractionStatus, MediaType
from app.models.sources import SourceVersion
from app.retrieval.embeddings import EMBEDDING_DIMENSIONS, MiniLMEmbeddingService

FIXTURES = Path(__file__).parents[1] / "fixtures" / "corpus"


class _Tokenizer:
    @staticmethod
    def tokenize(text: str) -> list[str]:
        return text.split()


class _EmbeddingBackend:
    tokenizer = _Tokenizer()

    @staticmethod
    def get_sentence_embedding_dimension() -> int:
        return EMBEDDING_DIMENSIONS

    @staticmethod
    def encode(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("chunking must use only the tokenizer")


def _embedding_service() -> MiniLMEmbeddingService:
    return MiniLMEmbeddingService(cache_dir=Path("/unused"), backend=_EmbeddingBackend())


def _document(content: bytes, *, topic: str = "registration") -> DiscoveredDocument:
    url = "https://www.pnw.edu/__test__/chunking/"
    return DiscoveredDocument(
        requested_url=url,
        canonical_url=url,
        title="Chunking fixture",
        topics=(topic,),
        media_type=MediaType.HTML,
        content=content,
        depth=0,
        parent_url=None,
        redirect_chain=(),
    )


def _version(parser_version: str) -> SourceVersion:
    return SourceVersion(
        id=uuid4(),
        source_id=uuid4(),
        content_sha256="a" * 64,
        content="synthetic public fixture",
        fetched_at=datetime(2030, 1, 15, tzinfo=UTC),
        extraction_status=ExtractionStatus.COMPLETE,
        parser_version=parser_version,
    )


def _chunk(
    content: bytes,
    *,
    topic: str = "registration",
    context: ChunkingContext | None = None,
) -> tuple[SourceVersion, ExtractedDocument, MiniLMEmbeddingService, ChunkingResult]:
    extracted = StructuredExtractor().extract(_document(content, topic=topic))
    version = _version(extracted.parser_version)
    service = _embedding_service()
    result = SemanticChunker(service.count_wordpieces).chunk(
        version=version,
        document=extracted,
        context=context or ChunkingContext(topic_key=topic),
    )
    return version, extracted, service, result


def test_heading_sections_produce_bounded_blocks_with_version_and_applicability_ids() -> None:
    html = b"""
        <main>
          <h1>Registration</h1>
          <section id="add"><h2>Add a class</h2><p>Submit the published add form.</p></section>
          <section id="drop"><h2>Drop a class</h2><p>Submit the published drop form.</p></section>
        </main>
    """
    context = ChunkingContext(
        topic_key="registration",
        campus="Hammond",
        term="Fall",
        session="Full Term",
        scope={"year": "2030"},
    )

    version, _, service, result = _chunk(html, context=context)

    assert [block.ordinal for block in result.blocks] == [0, 1]
    assert all(isinstance(block.id, UUID) for block in result.blocks)
    assert all(block.version_id == version.id for block in result.blocks)
    assert [block.heading_path for block in result.blocks] == [
        ["Registration", "Add a class"],
        ["Registration", "Drop a class"],
    ]
    assert all(block.scope == {"year": "2030"} for block in result.blocks)
    assert result.applicability.version_id == version.id
    assert result.applicability.campus == "Hammond"
    assert result.applicability.term == "Fall"
    assert result.applicability.session == "Full Term"
    assert result.applicability.evidence_block_ids == [block.id for block in result.blocks]
    assert result.wordpiece_counts == tuple(
        service.count_wordpieces(embedding_text(heading_path=block.heading_path, text=block.text))
        for block in result.blocks
    )


def test_each_table_body_row_keeps_headers_caption_and_footnotes() -> None:
    html = b"""
        <main>
          <h1>Academic schedule</h1>
          <table id="refunds">
            <caption>Hammond Fall 2030 Full Term</caption>
            <thead><tr><th scope="col">Event</th><th scope="col">Deadline</th></tr></thead>
            <tbody>
              <tr id="drop-100"><th scope="row">Drop 100%</th><td>September 6</td></tr>
              <tr id="drop-80"><th scope="row">Drop 80%</th><td>September 13</td></tr>
            </tbody>
            <tfoot><tr><td colspan="2">Dates use Central Time.</td></tr></tfoot>
          </table>
          <p class="table-note">Applies only to the stated term and session.</p>
        </main>
    """

    _, _, _, result = _chunk(
        html,
        topic="academic_schedule",
        context=ChunkingContext(
            topic_key="academic_schedule",
            campus="Hammond",
            term="Fall",
            session="Full Term",
        ),
    )

    assert len(result.blocks) == 2
    assert [block.anchor for block in result.blocks] == ["drop-100", "drop-80"]
    for block in result.blocks:
        assert "Table: Hammond Fall 2030 Full Term" in block.text
        assert "Column headers: Event | Deadline" in block.text
        assert "Footnote: Dates use Central Time." in block.text
        assert "Footnote: Applies only to the stated term and session." in block.text
        structured = block.structured_content
        assert isinstance(structured, dict)
        assert structured["type"] == "table_row"
        headers = structured["headers"]
        footnotes = structured["footnotes"]
        assert isinstance(headers, list) and len(headers) == 1
        assert isinstance(footnotes, list) and len(footnotes) == 2
    assert "Drop 100% | September 6" in result.blocks[0].text
    assert "Drop 80% | September 13" in result.blocks[1].text


def test_prerequisite_alternatives_and_corequisite_remain_one_protected_group() -> None:
    content = (FIXTURES / "us3" / "synthetic-computing-prerequisites.html").read_bytes()

    _, _, _, result = _chunk(content, topic="synthetic_course_prerequisites")

    assert len(result.blocks) == 1
    block = result.blocks[0]
    assert "SYN 11000 with a minimum grade of C" in block.text
    assert "SYN 12000 with a minimum grade of C" in block.text
    assert "or SYN 13000 with a minimum grade of C" in block.text
    assert "corequisite is SYN 25001" in block.text
    assert isinstance(block.structured_content, dict)
    assert block.structured_content["type"] == "prerequisite_group"
    assert block.structured_content["source_unit_indexes"] == [2, 3, 4]


def test_oversized_table_row_fails_instead_of_splitting_relationships() -> None:
    long_condition = " ".join(f"condition{number}" for number in range(230))
    html = f"""
        <main><h1>Schedule</h1><table>
          <thead><tr><th>Event</th><th>Condition</th></tr></thead>
          <tbody><tr><td>Drop</td><td>{long_condition}</td></tr></tbody>
        </table></main>
    """.encode()
    extracted = StructuredExtractor().extract(_document(html, topic="academic_schedule"))
    version = _version(extracted.parser_version)

    with pytest.raises(SemanticUnitTooLargeError) as captured:
        SemanticChunker(_embedding_service().count_wordpieces).chunk(
            version=version,
            document=extracted,
            context=ChunkingContext(
                topic_key="academic_schedule",
                term="Fall",
                session="Full Term",
            ),
        )

    assert captured.value.reason is ChunkingFailureReason.TABLE_ROW_TOO_LARGE
    assert captured.value.wordpiece_count > captured.value.maximum == 220


def test_oversized_prerequisite_group_fails_instead_of_splitting_alternatives() -> None:
    requirements = " ".join(f"SYN{number:05d}" for number in range(225))
    html = f"""
        <main><h1>Course requirements</h1><section id="requirements">
          <p>The prerequisite is one of {requirements}.</p>
        </section></main>
    """.encode()
    extracted = StructuredExtractor().extract(_document(html, topic="course_prerequisites"))
    version = _version(extracted.parser_version)

    with pytest.raises(SemanticUnitTooLargeError) as captured:
        SemanticChunker(_embedding_service().count_wordpieces).chunk(
            version=version,
            document=extracted,
            context=ChunkingContext(topic_key="course_prerequisites"),
        )

    assert captured.value.reason is ChunkingFailureReason.PREREQUISITE_GROUP_TOO_LARGE


def test_long_general_text_splits_only_at_sentence_boundaries() -> None:
    first = " ".join(f"alpha{number}" for number in range(150)) + "."
    second = " ".join(f"Beta{number}" for number in range(150)) + "."
    html = f"<main><h1>Policy</h1><p>{first} {second}</p></main>".encode()

    _, _, _, result = _chunk(html)

    assert len(result.blocks) == 2
    assert result.blocks[0].text == first
    assert result.blocks[1].text == second
    assert all(count <= 220 for count in result.wordpiece_counts)


def test_exact_220_wordpiece_boundary_is_allowed() -> None:
    text = " ".join(f"token{number}" for number in range(220))
    html = f"<main><p>{text}</p></main>".encode()

    _, _, _, result = _chunk(html)

    assert len(result.blocks) == 1
    assert result.wordpiece_counts == (220,)


def test_incomplete_extraction_cannot_produce_partial_chunks() -> None:
    html = b"""
        <main><h1>Broken schedule</h1><table>
          <tr><td>Event</td><td>Date</td></tr><tr><td>Drop</td></tr>
        </table></main>
    """
    extracted = StructuredExtractor().extract(_document(html))
    version = _version(extracted.parser_version)

    with pytest.raises(ChunkingInputError) as captured:
        SemanticChunker(_embedding_service().count_wordpieces).chunk(
            version=version,
            document=extracted,
            context=ChunkingContext(topic_key="registration"),
        )

    assert captured.value.reason is ChunkingFailureReason.INCOMPLETE_EXTRACTION


def test_unknown_scope_remains_null_and_is_never_promoted_to_all() -> None:
    html = b"<main><h1>General policy</h1><p>Read the published policy.</p></main>"

    _, _, _, result = _chunk(html)

    assert result.applicability.campus is None
    assert result.applicability.student_level is None
    assert result.applicability.program is None
    assert result.applicability.catalog_year is None
    assert result.applicability.term is None
    assert result.applicability.session is None
    assert result.blocks[0].scope == {}
