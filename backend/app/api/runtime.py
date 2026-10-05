"""Lifecycle-owned resources and fail-closed readiness checks."""

from __future__ import annotations

from typing import Protocol
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from app.config import SecretFileError, Settings

ELIGIBLE_CORPUS_QUERY = text(
    """
    SELECT EXISTS (
        SELECT 1
        FROM evidence_blocks AS evidence
        JOIN source_versions AS version ON version.id = evidence.version_id
        JOIN sources AS source ON source.id = version.source_id
        JOIN qualifications AS qualification ON qualification.version_id = version.id
        WHERE source.status = 'eligible'
          AND version.extraction_status = 'complete'
          AND qualification.status = 'passed'
          AND CURRENT_TIMESTAMP < qualification.valid_until
          AND (version.effective_from IS NULL OR version.effective_from <= CURRENT_TIMESTAMP)
          AND (version.effective_to IS NULL OR CURRENT_TIMESTAMP < version.effective_to)
          AND NOT EXISTS (
              SELECT 1
              FROM conflict_evidence_blocks AS disputed
              JOIN conflicts AS conflict ON conflict.id = disputed.conflict_id
              WHERE disputed.evidence_block_id = evidence.id
                AND conflict.status = 'unresolved'
          )
    )
    """
)


class ReadinessProbe(Protocol):
    """Injectable dependency used by the readiness endpoint."""

    def is_ready(self) -> bool:
        """Return whether all required serving dependencies are ready."""


class RuntimeReadinessProbe:
    """Check PostgreSQL corpus eligibility and optional local inference health."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._engine: Engine | None = None

    def startup(self) -> None:
        """Create the lazy connection pool; missing secrets leave readiness false."""

        try:
            database_url = self.settings.database_url()
        except SecretFileError:
            return
        self._engine = create_engine(
            database_url,
            connect_args={"connect_timeout": 2, "options": "-c statement_timeout=2000"},
            echo=False,
            hide_parameters=True,
            pool_pre_ping=True,
        )

    def shutdown(self) -> None:
        """Dispose database connections owned by this process."""

        if self._engine is not None:
            self._engine.dispose()
            self._engine = None

    def is_ready(self) -> bool:
        if self._engine is None:
            return False
        try:
            with self._engine.connect() as connection:
                eligible_corpus_exists = bool(
                    connection.execute(ELIGIBLE_CORPUS_QUERY).scalar_one()
                )
        except (SQLAlchemyError, OSError, ValueError):
            return False
        if not eligible_corpus_exists:
            return False
        return not self.settings.local_inference_enabled or self._local_inference_is_ready()

    def _local_inference_is_ready(self) -> bool:
        health_url = urljoin(str(self.settings.local_inference_base_url), "health")
        request = Request(health_url, method="GET")
        try:
            with urlopen(request, timeout=2) as response:
                return 200 <= int(response.status) < 300
        except (OSError, ValueError):
            return False
