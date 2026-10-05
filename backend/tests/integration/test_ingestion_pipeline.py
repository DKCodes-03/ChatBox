"""Production CLI and ingestion-coordinator coverage against PostgreSQL/pgvector."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import numpy as np
import pytest
from app.cli import EXIT_PRECONDITION, SourceOperator, main
from app.config import DatabaseRole, Settings
from app.ingestion.discovery import (
    DiscoveredDocument,
    DiscoveryFailureReason,
    DiscoveryIssue,
    DiscoveryPolicy,
    DiscoveryReport,
    SourceSeed,
)
from app.ingestion.pipeline import SourceIngestionPipeline
from app.models.enums import MediaType, SourceStatus
from app.models.operations import IngestionRun, SourceEvent
from app.models.sources import (
    Applicability,
    Embedding,
    EvidenceBlock,
    Qualification,
    Source,
    SourceLink,
    SourceVersion,
)
from app.retrieval.embeddings import EMBEDDING_DIMENSIONS, MiniLMEmbeddingService
from app.testing.embedding import deterministic_vector
from numpy.typing import NDArray
from sqlalchemy import create_engine, delete, func, or_, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration

FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "corpus" / "ingestion"
OBSERVED_AT = datetime(2030, 1, 16, 13, tzinfo=UTC)
DATABASE_INTEGRATION_ENABLED = os.environ.get("DATABASE_INTEGRATION_TESTS") == "true"
requires_database = pytest.mark.skipif(
    not DATABASE_INTEGRATION_ENABLED,
    reason="run through the Compose test-ingestion-pipeline service",
)


def _metadata() -> dict[str, dict[str, Any]]:
    payload = json.loads((FIXTURE_DIR / "fixture-metadata.json").read_text(encoding="utf-8"))
    return {
        cast(str, item["url"]): cast(dict[str, Any], item)
        for item in cast(list[dict[str, Any]], payload["sources"])
    }


SOURCES = _metadata()
LINKED_ROOT = "https://www.pnw.edu/__test__/ingestion/silver-compass/"
LINKED_DETAIL = "https://www.pnw.edu/__test__/ingestion/silver-compass/details/"
LINKED_PDF = "https://www.pnw.edu/__test__/ingestion/silver-compass/form.pdf"
MALFORMED = "https://www.pnw.edu/__test__/ingestion/broken-table/"
DUPLICATE = "https://www.pnw.edu/__test__/ingestion/idempotent-violet/"
TEST_URLS = (LINKED_ROOT, LINKED_DETAIL, LINKED_PDF, MALFORMED, DUPLICATE)


class _Tokenizer:
    @staticmethod
    def tokenize(text: str) -> list[str]:
        return text.split()


class _EmbeddingBackend:
    tokenizer = _Tokenizer()

    @staticmethod
    def get_sentence_embedding_dimension() -> int:
        return EMBEDDING_DIMENSIONS

    def encode(
        self,
        inputs: Sequence[str],
        *,
        batch_size: int,
        show_progress_bar: bool,
        convert_to_numpy: bool,
        convert_to_tensor: bool,
        normalize_embeddings: bool,
    ) -> NDArray[np.float32]:
        del batch_size, show_progress_bar, convert_to_numpy, convert_to_tensor
        assert normalize_embeddings
        return np.stack(tuple(deterministic_vector(text) for text in inputs))


def _embedding_service() -> MiniLMEmbeddingService:
    return MiniLMEmbeddingService(cache_dir=Path("/unused"), backend=_EmbeddingBackend())


@dataclass(slots=True)
class _Clock:
    current: datetime = OBSERVED_AT

    def __call__(self) -> datetime:
        return self.current


class _FixtureDiscovery:
    def __init__(self) -> None:
        self.fail = False
        self.changed_duplicate = False

    def __call__(
        self,
        policy: DiscoveryPolicy,
        seeds: Sequence[SourceSeed],
    ) -> DiscoveryReport:
        del policy
        seed = seeds[0]
        if self.fail:
            return DiscoveryReport(
                documents=(),
                issues=(
                    DiscoveryIssue(
                        url=seed.url,
                        depth=0,
                        reason=DiscoveryFailureReason.FETCH_UNAVAILABLE,
                    ),
                ),
                attempted_urls=(seed.url,),
                truncated=False,
            )
        urls = (LINKED_ROOT, LINKED_DETAIL, LINKED_PDF) if seed.url == LINKED_ROOT else (seed.url,)
        documents = tuple(self._document(url, root=seed.url) for url in urls)
        return DiscoveryReport(
            documents=documents,
            issues=(),
            attempted_urls=urls,
            truncated=False,
        )

    def _document(self, url: str, *, root: str) -> DiscoveredDocument:
        metadata = SOURCES[url]
        content = (FIXTURE_DIR / cast(str, metadata["fixture_file"])).read_bytes()
        if url == DUPLICATE and self.changed_duplicate:
            content = content.replace(b"VIOLET-44", b"VIOLET-45")
        return DiscoveredDocument(
            requested_url=url,
            canonical_url=url,
            title=cast(str, metadata["title"]),
            topics=(cast(str, metadata["applicability"]["topic"]),)
            if metadata["applicability"] is not None
            else ("synthetic_malformed_table",),
            media_type=MediaType(cast(str, metadata["media_type"])),
            content=content,
            depth=0 if url == root else 1,
            parent_url=None if url == root else root,
            redirect_chain=(),
        )


@dataclass(slots=True)
class _Database:
    settings: Settings
    migration_engine: Engine
    governance_engine: Engine

    def cleanup(self) -> None:
        with Session(self.migration_engine) as session, session.begin():
            source_ids = tuple(
                session.scalars(select(Source.id).where(Source.canonical_url.in_(TEST_URLS))).all()
            )
            if not source_ids:
                return
            version_ids = select(SourceVersion.id).where(SourceVersion.source_id.in_(source_ids))
            evidence_ids = select(EvidenceBlock.id).where(EvidenceBlock.version_id.in_(version_ids))
            session.execute(
                delete(SourceLink).where(
                    or_(
                        SourceLink.from_version_id.in_(version_ids),
                        SourceLink.target_source_id.in_(source_ids),
                    )
                )
            )
            session.execute(delete(Embedding).where(Embedding.block_id.in_(evidence_ids)))
            session.execute(delete(Applicability).where(Applicability.version_id.in_(version_ids)))
            session.execute(delete(Qualification).where(Qualification.version_id.in_(version_ids)))
            session.execute(delete(EvidenceBlock).where(EvidenceBlock.id.in_(evidence_ids)))
            session.execute(delete(IngestionRun).where(IngestionRun.source_id.in_(source_ids)))
            session.execute(delete(SourceEvent).where(SourceEvent.source_id.in_(source_ids)))
            session.execute(delete(SourceVersion).where(SourceVersion.source_id.in_(source_ids)))
            session.execute(delete(Source).where(Source.id.in_(source_ids)))


@pytest.fixture
def database() -> Iterator[_Database]:
    if not DATABASE_INTEGRATION_ENABLED:
        pytest.skip("run through the Compose test-ingestion-pipeline service")
    settings = Settings()
    database = _Database(
        settings=settings,
        migration_engine=create_engine(settings.database_url(DatabaseRole.MIGRATION)),
        governance_engine=create_engine(settings.database_url(DatabaseRole.GOVERNANCE)),
    )
    database.cleanup()
    try:
        yield database
    finally:
        database.cleanup()
        database.governance_engine.dispose()
        database.migration_engine.dispose()


def _manifest(tmp_path: Path) -> Path:
    path = tmp_path / "sources.json"
    sources = []
    for url in (LINKED_ROOT, MALFORMED, DUPLICATE):
        metadata = SOURCES[url]
        topics = (
            [metadata["applicability"]["topic"]]
            if metadata["applicability"] is not None
            else ["synthetic_malformed_table"]
        )
        sources.append(
            {
                "url": url,
                "title": metadata["title"],
                "topics": topics,
                "allowed_child_hosts": ["www.pnw.edu"],
            }
        )
    path.write_text(json.dumps({"sources": sources}), encoding="utf-8")
    return path


def _run(operator: SourceOperator, *arguments: str) -> tuple[int, dict[str, object]]:
    output = StringIO()
    exit_code = main(("sources", *arguments), source_operator=operator, stdout=output)
    return exit_code, cast(dict[str, object], json.loads(output.getvalue()))


@requires_database
def test_real_cli_imports_idempotently_and_ingests_linked_html_pdf_and_quarantine(
    database: _Database,
    tmp_path: Path,
) -> None:
    clock = _Clock()
    discovery = _FixtureDiscovery()
    pipeline = SourceIngestionPipeline(
        database.governance_engine,
        settings=database.settings,
        embedding_service=_embedding_service(),
        discovery_runner=discovery,
        clock=clock,
    )
    operator = SourceOperator(
        database.governance_engine,
        settings=database.settings,
        ingestion_pipeline=pipeline,
    )
    manifest = _manifest(tmp_path)

    first_exit, first = _run(operator, "import", "--manifest", str(manifest))
    second_exit, second = _run(operator, "import", "--manifest", str(manifest))
    assert first_exit == second_exit == 0
    assert len(cast(list[str], first["created_source_ids"])) == 3
    assert second["created_source_ids"] == []
    source_ids = {
        url: UUID(identifier)
        for url, identifier in zip(
            (LINKED_ROOT, MALFORMED, DUPLICATE),
            cast(list[str], first["source_ids"]),
            strict=True,
        )
    }

    linked_exit, linked = _run(
        operator,
        "fetch",
        "--source-id",
        str(source_ids[LINKED_ROOT]),
    )
    assert linked_exit == 0, linked
    assert linked["status"] == SourceStatus.ELIGIBLE.value
    assert len(cast(list[str], linked["document_source_ids"])) == 3

    malformed_exit, malformed = _run(
        operator,
        "fetch",
        "--source-id",
        str(source_ids[MALFORMED]),
    )
    assert malformed_exit == EXIT_PRECONDITION
    assert malformed["status"] == SourceStatus.QUARANTINED.value
    assert "malformed_table" in cast(list[str], malformed["reason_codes"])

    with Session(database.migration_engine) as session:
        linked_ids = tuple(
            session.scalars(select(Source.id).where(Source.canonical_url.in_(TEST_URLS[:3]))).all()
        )
        linked_versions = select(SourceVersion.id).where(SourceVersion.source_id.in_(linked_ids))
        assert len(linked_ids) == 3
        assert (
            session.scalar(
                select(func.count())
                .select_from(SourceVersion)
                .where(SourceVersion.source_id.in_(linked_ids))
            )
            == 3
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(Embedding)
                .join(EvidenceBlock, EvidenceBlock.id == Embedding.block_id)
                .where(EvidenceBlock.version_id.in_(linked_versions))
            )
            or 0
        ) > 0
        assert (session.scalar(select(func.count()).select_from(SourceLink)) or 0) >= 2
        malformed_version_ids = select(SourceVersion.id).where(
            SourceVersion.source_id == source_ids[MALFORMED]
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(EvidenceBlock)
                .where(EvidenceBlock.version_id.in_(malformed_version_ids))
            )
            == 0
        )


@requires_database
def test_material_change_failed_refresh_withdraw_restore_and_requalification(
    database: _Database,
    tmp_path: Path,
) -> None:
    clock = _Clock()
    discovery = _FixtureDiscovery()
    pipeline = SourceIngestionPipeline(
        database.governance_engine,
        settings=database.settings,
        embedding_service=_embedding_service(),
        discovery_runner=discovery,
        clock=clock,
    )
    operator = SourceOperator(
        database.governance_engine,
        settings=database.settings,
        ingestion_pipeline=pipeline,
    )
    _exit, imported = _run(operator, "import", "--manifest", str(_manifest(tmp_path)))
    duplicate_id = UUID(cast(list[str], imported["source_ids"])[2])

    assert _run(operator, "fetch", "--source-id", str(duplicate_id))[0] == 0
    discovery.changed_duplicate = True
    clock.current += timedelta(hours=1)
    changed_exit, changed = _run(operator, "fetch", "--source-id", str(duplicate_id))
    assert changed_exit == 0
    assert changed["status"] == SourceStatus.ELIGIBLE.value
    with Session(database.migration_engine) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(SourceVersion)
                .where(SourceVersion.source_id == duplicate_id)
            )
            == 2
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(Qualification)
                .join(SourceVersion, SourceVersion.id == Qualification.version_id)
                .where(SourceVersion.source_id == duplicate_id)
            )
            == 2
        )

    discovery.fail = True
    clock.current += timedelta(hours=25)
    refresh_exit, refresh = _run(operator, "refresh-due")
    assert refresh_exit == EXIT_PRECONDITION
    assert str(duplicate_id) in cast(list[str], refresh["failed_source_ids"])
    with Session(database.migration_engine) as session:
        assert session.get(Source, duplicate_id).status is SourceStatus.STALE  # type: ignore[union-attr]

    withdraw_exit, withdrawn = _run(
        operator,
        "withdraw",
        "--source-id",
        str(duplicate_id),
        "--reason",
        "synthetic_withdrawal",
    )
    restore_exit, restored = _run(operator, "restore", "--source-id", str(duplicate_id))
    assert withdraw_exit == restore_exit == 0
    assert withdrawn["status"] == SourceStatus.WITHDRAWN.value
    assert restored["status"] == SourceStatus.STALE.value

    discovery.fail = False
    clock.current += timedelta(hours=1)
    requalify_exit, requalified = _run(
        operator,
        "fetch",
        "--source-id",
        str(duplicate_id),
    )
    assert requalify_exit == 0
    assert requalified["status"] == SourceStatus.ELIGIBLE.value
