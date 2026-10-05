"""Load the synthetic US1 HTML, linked page, and PDF into PostgreSQL."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from bs4 import BeautifulSoup
from pypdf import PdfReader
from sqlalchemy import create_engine, text

from app.config import DatabaseRole, Settings
from app.testing.embedding import deterministic_vector


def _load_metadata(fixture_dir: Path) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        json.loads((fixture_dir / "fixture-metadata.json").read_text(encoding="utf-8")),
    )


def _html_evidence(path: Path, anchor: str) -> str:
    section = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser").find(id=anchor)
    if section is None:
        raise ValueError("synthetic HTML fixture is missing an evidence anchor")
    return " ".join(section.get_text(" ", strip=True).split())


def _pdf_evidence(path: Path, page: int) -> str:
    extracted = PdfReader(path).pages[page - 1].extract_text()
    if extracted is None:
        raise ValueError("synthetic PDF fixture page has no extractable text")
    return " ".join(extracted.split())


def _source_content(path: Path, media_type: str) -> str:
    if media_type == "html":
        return path.read_text(encoding="utf-8")
    pages = tuple(page.extract_text() or "" for page in PdfReader(path).pages)
    content = "\n\n".join(pages).strip()
    if not content:
        raise ValueError("synthetic PDF fixture has no extractable content")
    return content


def seed(fixture_dir: Path, settings: Settings) -> None:
    """Upsert one qualified synthetic corpus using migration-role credentials."""

    metadata = _load_metadata(fixture_dir)
    applicability = metadata["applicability"]
    checked_at = datetime.now(UTC)
    valid_until = checked_at + timedelta(hours=24)
    engine = create_engine(settings.database_url(DatabaseRole.MIGRATION), hide_parameters=True)

    try:
        with engine.begin() as connection:
            for source in metadata["sources"]:
                source_id = UUID(source["source_id"])
                version_id = UUID(source["version_id"])
                fixture_path = fixture_dir / source["fixture_file"]
                source_content = _source_content(fixture_path, source["media_type"])
                connection.execute(
                    text(
                        """
                        INSERT INTO sources (id, canonical_url, title, media_type, status)
                        VALUES (:id, :url, :title, :media_type, 'eligible')
                        ON CONFLICT (id) DO UPDATE SET
                          canonical_url = EXCLUDED.canonical_url,
                          title = EXCLUDED.title,
                          media_type = EXCLUDED.media_type,
                          status = EXCLUDED.status
                        """
                    ),
                    {
                        "id": source_id,
                        "url": source["url"],
                        "title": source["title"],
                        "media_type": source["media_type"],
                    },
                )
                connection.execute(
                    text(
                        """
                        INSERT INTO source_versions (
                          id, source_id, content_sha256, content, fetched_at,
                          extraction_status, parser_version
                        ) VALUES (
                          :id, :source_id, :content_sha256, :content, :fetched_at,
                          'complete', 'synthetic-us1-v1'
                        )
                        ON CONFLICT (id) DO UPDATE SET
                          content_sha256 = EXCLUDED.content_sha256,
                          content = EXCLUDED.content,
                          fetched_at = EXCLUDED.fetched_at,
                          extraction_status = EXCLUDED.extraction_status,
                          parser_version = EXCLUDED.parser_version
                        """
                    ),
                    {
                        "id": version_id,
                        "source_id": source_id,
                        "content_sha256": source["content_sha256"],
                        "content": source_content,
                        "fetched_at": checked_at,
                    },
                )

                evidence_ids: list[UUID] = []
                for evidence in source["evidence"]:
                    evidence_id = UUID(evidence["evidence_id"])
                    evidence_ids.append(evidence_id)
                    evidence_text = (
                        _html_evidence(fixture_path, evidence["anchor"])
                        if source["media_type"] == "html"
                        else _pdf_evidence(fixture_path, evidence["page"])
                    )
                    if not all(marker in evidence_text for marker in evidence["text_contains"]):
                        raise ValueError("synthetic fixture no longer matches its metadata")
                    connection.execute(
                        text(
                            """
                            INSERT INTO evidence_blocks (
                              id, version_id, ordinal, heading_path, page, anchor, text,
                              structured_content, topic_key, scope
                            ) VALUES (
                              :id, :version_id, :ordinal, :heading_path, :page, :anchor, :text,
                              CAST(:structured_content AS jsonb), :topic_key, CAST(:scope AS jsonb)
                            )
                            ON CONFLICT (id) DO UPDATE SET
                              ordinal = EXCLUDED.ordinal,
                              heading_path = EXCLUDED.heading_path,
                              page = EXCLUDED.page,
                              anchor = EXCLUDED.anchor,
                              text = EXCLUDED.text,
                              structured_content = EXCLUDED.structured_content,
                              topic_key = EXCLUDED.topic_key,
                              scope = EXCLUDED.scope
                            """
                        ),
                        {
                            "id": evidence_id,
                            "version_id": version_id,
                            "ordinal": evidence["ordinal"],
                            "heading_path": evidence["heading_path"],
                            "page": evidence.get("page"),
                            "anchor": evidence.get("anchor"),
                            "text": evidence_text,
                            "structured_content": "{}",
                            "topic_key": evidence["topic_key"],
                            "scope": "{}",
                        },
                    )
                    embedded_text = "\n".join((*evidence["heading_path"], evidence_text))
                    vector = deterministic_vector(embedded_text)
                    vector_literal = "[" + ",".join(str(float(value)) for value in vector) + "]"
                    connection.execute(
                        text(
                            """
                            INSERT INTO embeddings (block_id, model_revision, dimensions, vector)
                            VALUES (:block_id, :model_revision, 384, CAST(:vector AS vector))
                            ON CONFLICT (block_id, model_revision) DO UPDATE SET
                              dimensions = EXCLUDED.dimensions,
                              vector = EXCLUDED.vector
                            """
                        ),
                        {
                            "block_id": evidence_id,
                            "model_revision": settings.embedding_model,
                            "vector": vector_literal,
                        },
                    )

                connection.execute(
                    text(
                        """
                        INSERT INTO applicability (
                          version_id, topic, institution, campus, student_level, program,
                          catalog_year, term, session, evidence_block_ids
                        ) VALUES (
                          :version_id, :topic, :institution, :campus, :student_level, :program,
                          :catalog_year, :term, :session, :evidence_block_ids
                        )
                        ON CONFLICT (version_id, topic) DO UPDATE SET
                          institution = EXCLUDED.institution,
                          campus = EXCLUDED.campus,
                          student_level = EXCLUDED.student_level,
                          program = EXCLUDED.program,
                          catalog_year = EXCLUDED.catalog_year,
                          term = EXCLUDED.term,
                          session = EXCLUDED.session,
                          evidence_block_ids = EXCLUDED.evidence_block_ids
                        """
                    ),
                    {
                        "version_id": version_id,
                        "topic": applicability["topic"],
                        "institution": applicability["institution"],
                        "campus": applicability["campus"],
                        "student_level": applicability["student_level"],
                        "program": applicability["program"],
                        "catalog_year": applicability["catalog_year"],
                        "term": applicability["term"],
                        "session": applicability["session"],
                        "evidence_block_ids": evidence_ids,
                    },
                )
                qualification = source["qualification"]
                connection.execute(
                    text(
                        """
                        INSERT INTO qualifications (
                          id, version_id, rule_version, checked_at, valid_until, status,
                          check_results, provenance_evidence, applicability_evidence
                        ) VALUES (
                          :id, :version_id, :rule_version, :checked_at, :valid_until, 'passed',
                          CAST(:check_results AS jsonb), CAST(:provenance AS jsonb),
                          CAST(:applicability AS jsonb)
                        )
                        ON CONFLICT (id) DO UPDATE SET
                          checked_at = EXCLUDED.checked_at,
                          valid_until = EXCLUDED.valid_until,
                          status = EXCLUDED.status,
                          check_results = EXCLUDED.check_results,
                          provenance_evidence = EXCLUDED.provenance_evidence,
                          applicability_evidence = EXCLUDED.applicability_evidence
                        """
                    ),
                    {
                        "id": UUID(source["qualification_id"]),
                        "version_id": version_id,
                        "rule_version": metadata["rule_version"],
                        "checked_at": checked_at,
                        "valid_until": valid_until,
                        "check_results": json.dumps(qualification, sort_keys=True),
                        "provenance": json.dumps({"synthetic": True}, sort_keys=True),
                        "applicability": json.dumps(applicability, sort_keys=True),
                    },
                )

            for source in metadata["sources"]:
                for link in source["links"]:
                    connection.execute(
                        text(
                            """
                            INSERT INTO source_links (from_version_id, target_source_id, relation)
                            VALUES (:from_version_id, :target_source_id, :relation)
                            ON CONFLICT (from_version_id, target_source_id, relation) DO NOTHING
                            """
                        ),
                        {
                            "from_version_id": UUID(source["version_id"]),
                            "target_source_id": UUID(link["target_source_id"]),
                            "relation": link["relation"],
                        },
                    )
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the synthetic US1 qualified corpus")
    parser.add_argument("--fixture-dir", type=Path, default=Path("/fixtures/us1"))
    arguments = parser.parse_args()
    seed(arguments.fixture_dir, Settings())


if __name__ == "__main__":
    main()
