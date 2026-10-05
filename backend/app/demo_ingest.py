"""One-command offline preparation of the checked-in public PNW demo corpus."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import IO
from uuid import UUID

from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import DatabaseRole, SecretFileError, Settings
from app.ingestion.pipeline import IngestionPipelineError, SourceIngestionPipeline
from app.models.sources import Embedding, EvidenceBlock, SourceVersion


def _embedding_count(session: Session, source_id: UUID) -> int:
    count = session.scalar(
        select(func.count())
        .select_from(Embedding)
        .join(EvidenceBlock, EvidenceBlock.id == Embedding.block_id)
        .join(SourceVersion, SourceVersion.id == EvidenceBlock.version_id)
        .where(SourceVersion.source_id == source_id)
    )
    return int(count or 0)


def main(argv: Sequence[str] | None = None, *, stdout: IO[str] | None = None) -> int:
    """Import, fetch, parse, chunk, embed, qualify, and verify every manifest root."""

    parser = argparse.ArgumentParser(prog="python -m app.demo_ingest")
    parser.add_argument("--manifest", type=Path, required=True)
    arguments = parser.parse_args(argv)
    output = stdout or sys.stdout
    engine = None
    try:
        settings = Settings()
        engine = create_engine(
            settings.database_url(DatabaseRole.GOVERNANCE),
            connect_args={"connect_timeout": 3, "options": "-c statement_timeout=120000"},
            hide_parameters=True,
            pool_pre_ping=True,
        )
        pipeline = SourceIngestionPipeline(engine, settings=settings)
        imported = pipeline.import_manifest(arguments.manifest)
        records: list[dict[str, object]] = []
        all_prepared = True
        for source_id in imported.source_ids:
            result = pipeline.fetch(source_id)
            requested = next(
                (item for item in result.documents if item.source_id == source_id),
                None,
            )
            with Session(engine) as session:
                vector_count = _embedding_count(session, source_id)
            prepared = requested is not None and vector_count > 0
            all_prepared = all_prepared and prepared
            record: dict[str, object] = {
                "source_id": str(source_id),
                "prepared": prepared,
                "embedding_count": vector_count,
                "reason_codes": list(result.reason_codes),
            }
            if requested is not None:
                record.update(
                    {
                        "status": requested.status.value,
                        "version_id": str(requested.version_id),
                        "qualification_id": str(requested.qualification_id),
                    }
                )
            records.append(record)
        payload: dict[str, object] = {
            "status": "success" if all_prepared else "incomplete",
            "manifest": str(arguments.manifest),
            "source_count": len(imported.source_ids),
            "created_source_count": len(imported.created_source_ids),
            "sources": records,
        }
        exit_code = 0 if all_prepared else 4
    except (IngestionPipelineError, SecretFileError, SQLAlchemyError):
        payload = {
            "status": "error",
            "reason_codes": ["demo_ingestion_failed"],
        }
        exit_code = 5
    finally:
        if engine is not None:
            engine.dispose()
    output.write(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    output.write("\n")
    output.flush()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
