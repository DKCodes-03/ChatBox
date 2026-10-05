"""Foundational checks for the PostgreSQL/pgvector migration chain."""

from __future__ import annotations

from collections.abc import Callable
from importlib import import_module
from types import ModuleType
from typing import cast

import sqlalchemy as sa
from app.models.sources import SourceLink
from pgvector.sqlalchemy import VECTOR
from pytest import MonkeyPatch

EXPECTED_TABLES = {
    "aggregate_metrics",
    "applicability",
    "conflict_evidence_blocks",
    "conflicts",
    "course_relations",
    "embeddings",
    "evidence_blocks",
    "ingestion_runs",
    "office_referrals",
    "qualifications",
    "source_events",
    "source_links",
    "source_versions",
    "sources",
}
FORBIDDEN_CHAT_TABLE_TERMS = {"chat", "conversation", "message", "query"}
FORBIDDEN_PRIVATE_COLUMNS = {
    "assistant_message",
    "conversation_id",
    "message_text",
    "query_embedding",
    "query_vector",
    "session_token",
    "student_message",
}


class _OperationRecorder:
    def __init__(self) -> None:
        self.statements: list[str] = []
        self.tables: dict[str, tuple[object, ...]] = {}
        self.indexes: list[tuple[str, str, tuple[str, ...], dict[str, object]]] = []
        self.dropped_tables: list[str] = []
        self.added_columns: list[tuple[str, sa.Column[object]]] = []
        self.check_constraints: list[tuple[str, str, str]] = []

    @staticmethod
    def f(name: str) -> str:
        return name

    def execute(self, statement: str) -> None:
        self.statements.append(statement)

    def create_table(self, name: str, *items: object, **_kwargs: object) -> None:
        self.tables[name] = items

    def create_index(
        self,
        name: str,
        table_name: str,
        columns: list[str],
        **kwargs: object,
    ) -> None:
        self.indexes.append((name, table_name, tuple(columns), kwargs))

    def drop_table(self, name: str) -> None:
        self.dropped_tables.append(name)

    def add_column(self, table_name: str, column: sa.Column[object]) -> None:
        self.added_columns.append((table_name, column))

    def create_check_constraint(self, name: str, table_name: str, condition: str) -> None:
        self.check_constraints.append((name, table_name, condition))


def _migration(name: str) -> ModuleType:
    return import_module(f"migrations.versions.{name}")


def _run_upgrade(
    module: ModuleType, recorder: _OperationRecorder, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setattr(module, "op", recorder)
    cast(Callable[[], None], module.upgrade)()


def _columns(items: tuple[object, ...]) -> dict[str, sa.Column[object]]:
    return {
        item.name: item
        for item in items
        if isinstance(item, sa.Column) and isinstance(item.name, str)
    }


def _constraints(items: tuple[object, ...]) -> tuple[str, ...]:
    return tuple(str(item.sqltext) for item in items if isinstance(item, sa.CheckConstraint))


def test_revision_chain_enables_pgvector_before_source_schema(monkeypatch: MonkeyPatch) -> None:
    extension = _migration("001_enable_pgvector")
    schema = _migration("002_source_schema")
    answer_locks = _migration("003_answer_source_locks")
    ingestion_configuration = _migration("004_source_ingestion_configuration")
    extension_ops = _OperationRecorder()

    _run_upgrade(extension, extension_ops, monkeypatch)

    assert extension.revision == "001_enable_pgvector"
    assert extension.down_revision is None
    assert schema.revision == "002_source_schema"
    assert schema.down_revision == "001_enable_pgvector"
    assert answer_locks.revision == "003_answer_source_locks"
    assert answer_locks.down_revision == "002_source_schema"
    assert ingestion_configuration.revision == "004_source_ingestion_config"
    assert ingestion_configuration.down_revision == "003_answer_source_locks"
    assert extension_ops.statements == ["CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public"]


def test_source_ingestion_configuration_is_bounded_and_nonnullable(
    monkeypatch: MonkeyPatch,
) -> None:
    migration = _migration("004_source_ingestion_configuration")
    operations = _OperationRecorder()

    _run_upgrade(migration, operations, monkeypatch)

    columns = {
        column.name: column for table, column in operations.added_columns if table == "sources"
    }
    assert set(columns) == {"configured_topics", "allowed_child_hosts"}
    assert all(column.nullable is False for column in columns.values())
    rendered_checks = " ".join(
        condition for _name, _table, condition in operations.check_constraints
    )
    assert "cardinality(configured_topics) BETWEEN 0 AND 16" in rendered_checks
    assert "cardinality(allowed_child_hosts) BETWEEN 0 AND 3" in rendered_checks


def test_answer_lock_function_is_definer_scoped_and_not_public(
    monkeypatch: MonkeyPatch,
) -> None:
    migration = _migration("003_answer_source_locks")
    operations = _OperationRecorder()

    _run_upgrade(migration, operations, monkeypatch)

    rendered = " ".join(operations.statements)
    assert "SECURITY DEFINER" in rendered
    assert "SET search_path = pg_catalog, public" in rendered
    assert "ORDER BY source.id" in rendered
    assert "FOR SHARE" in rendered
    assert "REVOKE ALL ON FUNCTION" in rendered


def test_source_schema_contains_only_public_corpus_and_aggregate_tables(
    monkeypatch: MonkeyPatch,
) -> None:
    schema = _migration("002_source_schema")
    operations = _OperationRecorder()

    _run_upgrade(schema, operations, monkeypatch)

    assert set(operations.tables) == EXPECTED_TABLES
    table_names = {name.casefold() for name in operations.tables}
    column_names = {
        name.casefold() for items in operations.tables.values() for name in _columns(items)
    }
    assert all(
        forbidden not in table_name
        for table_name in table_names
        for forbidden in FORBIDDEN_CHAT_TABLE_TERMS
    )
    assert column_names.isdisjoint(FORBIDDEN_PRIVATE_COLUMNS)


def test_embedding_and_freshness_constraints_match_the_retrieval_contract(
    monkeypatch: MonkeyPatch,
) -> None:
    schema = _migration("002_source_schema")
    operations = _OperationRecorder()
    _run_upgrade(schema, operations, monkeypatch)

    embedding_columns = _columns(operations.tables["embeddings"])
    vector_type = embedding_columns["vector"].type
    assert isinstance(vector_type, VECTOR)
    assert vector_type.dim == 384
    assert embedding_columns["vector"].nullable is False

    qualification_checks = " ".join(_constraints(operations.tables["qualifications"]))
    assert "valid_until >= checked_at" in qualification_checks
    assert "INTERVAL '24 hours'" in qualification_checks

    source_checks = " ".join(_constraints(operations.tables["sources"]))
    evidence_checks = " ".join(_constraints(operations.tables["evidence_blocks"]))
    assert "canonical_url ~ '^https://'" in source_checks
    assert "jsonb_typeof(structured_content) IN ('object', 'array')" in evidence_checks

    index_names = {name for name, _table, _columns, _kwargs in operations.indexes}
    assert "ix_qualifications_fresh_passed" in index_names
    assert "ix_applicability_topic_institution_version" in index_names
    assert "ix_embeddings_model_revision_block" in index_names
    rendered_indexes = repr(operations.indexes).casefold()
    assert "hnsw" not in rendered_indexes
    assert "ivfflat" not in rendered_indexes


def test_source_links_preserve_discovery_provenance_without_conferring_eligibility(
    monkeypatch: MonkeyPatch,
) -> None:
    schema = _migration("002_source_schema")
    operations = _OperationRecorder()
    _run_upgrade(schema, operations, monkeypatch)

    columns = _columns(operations.tables["source_links"])
    assert set(columns) == {"from_version_id", "target_source_id", "relation"}
    assert (
        sum(isinstance(item, sa.PrimaryKeyConstraint) for item in operations.tables["source_links"])
        == 1
    )
    source_link_table = cast(sa.Table, SourceLink.__table__)
    assert set(source_link_table.primary_key.columns.keys()) == {
        "from_version_id",
        "target_source_id",
        "relation",
    }
    assert "status" not in columns
    assert "qualification" not in columns


def test_source_schema_downgrade_reverses_every_created_table(monkeypatch: MonkeyPatch) -> None:
    schema = _migration("002_source_schema")
    operations = _OperationRecorder()
    monkeypatch.setattr(schema, "op", operations)

    cast(Callable[[], None], schema.downgrade)()

    assert set(operations.dropped_tables) == EXPECTED_TABLES
    assert len(operations.dropped_tables) == len(EXPECTED_TABLES)
