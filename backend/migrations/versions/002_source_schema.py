"""Create the qualified public-source and aggregate-metric schema.

Revision ID: 002_source_schema
Revises: 001_enable_pgvector
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import VECTOR
from sqlalchemy.dialects import postgresql

revision: str = "002_source_schema"
down_revision: str | None = "001_enable_pgvector"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create constrained tables and exact-retrieval support indexes."""

    op.create_table(
        "aggregate_metrics",
        sa.Column("hour_bucket", sa.DateTime(timezone=True), nullable=False),
        sa.Column("bounded_metric_name", sa.String(length=12), nullable=False),
        sa.Column("outcome_enum", sa.String(length=13), nullable=False),
        sa.Column("count", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("sum_ms", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column(
            "histogram_buckets",
            postgresql.ARRAY(sa.BigInteger()),
            server_default=sa.text("'{}'::bigint[]"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "bounded_metric_name IN ('chat_request', 'retrieval', 'generation')",
            name=op.f("ck_aggregate_metrics_metric_name"),
        ),
        sa.CheckConstraint(
            "outcome_enum IN "
            "('answer', 'partial', 'clarification', 'unable', 'rejected', 'error')",
            name=op.f("ck_aggregate_metrics_metric_outcome"),
        ),
        sa.CheckConstraint(
            "hour_bucket = date_trunc('hour', hour_bucket, 'UTC')",
            name=op.f("ck_aggregate_metrics_hour_bucket_aligned"),
        ),
        sa.CheckConstraint("count >= 0", name=op.f("ck_aggregate_metrics_count_nonnegative")),
        sa.CheckConstraint("sum_ms >= 0", name=op.f("ck_aggregate_metrics_sum_ms_nonnegative")),
        sa.CheckConstraint(
            "0 <= ALL (histogram_buckets)",
            name=op.f("ck_aggregate_metrics_histogram_nonnegative"),
        ),
        sa.PrimaryKeyConstraint(
            "hour_bucket",
            "bounded_metric_name",
            "outcome_enum",
            name="pk_aggregate_metrics",
        ),
    )
    op.create_index(
        "ix_aggregate_metrics_name_outcome_hour",
        "aggregate_metrics",
        ["bounded_metric_name", "outcome_enum", "hour_bucket"],
        unique=False,
    )

    op.create_table(
        "conflicts",
        sa.Column("topic_key", sa.String(length=128), nullable=False),
        sa.Column(
            "scope",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=10), server_default="unresolved", nullable=False),
        sa.Column(
            "detected_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "supersession_evidence_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            server_default=sa.text("'{}'::uuid[]"),
            nullable=False,
        ),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('unresolved', 'resolved')",
            name=op.f("ck_conflicts_conflict_status"),
        ),
        sa.CheckConstraint(
            "resolved_at IS NULL OR resolved_at >= detected_at",
            name=op.f("ck_conflicts_resolution_after_detection"),
        ),
        sa.CheckConstraint(
            "(status = 'unresolved' AND resolved_at IS NULL "
            "AND cardinality(supersession_evidence_ids) = 0) OR "
            "(status = 'resolved' AND resolved_at IS NOT NULL "
            "AND cardinality(supersession_evidence_ids) > 0)",
            name=op.f("ck_conflicts_resolution_is_source_backed"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(scope) = 'object'",
            name=op.f("ck_conflicts_scope_object"),
        ),
        sa.CheckConstraint(
            "length(btrim(topic_key)) > 0",
            name=op.f("ck_conflicts_topic_key_not_blank"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_conflicts"),
    )

    op.create_table(
        "sources",
        sa.Column("canonical_url", sa.String(length=2048), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("media_type", sa.String(length=4), nullable=False),
        sa.Column("status", sa.String(length=11), server_default="candidate", nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.CheckConstraint(
            "canonical_url ~ '^https://'",
            name=op.f("ck_sources_canonical_url_https"),
        ),
        sa.CheckConstraint(
            "media_type IN ('html', 'pdf')",
            name=op.f("ck_sources_media_type"),
        ),
        sa.CheckConstraint(
            "status IN ('candidate', 'eligible', 'quarantined', 'stale', 'withdrawn')",
            name=op.f("ck_sources_source_status"),
        ),
        sa.CheckConstraint(
            "length(btrim(title)) > 0",
            name=op.f("ck_sources_title_not_blank"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_sources"),
        sa.UniqueConstraint("canonical_url", name="uq_sources_canonical_url"),
    )

    op.create_table(
        "source_versions",
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "extraction_status",
            sa.String(length=10),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("parser_version", sa.String(length=128), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_source_versions_content_sha256_lower_hex"),
        ),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_from IS NULL OR effective_to >= effective_from",
            name=op.f("ck_source_versions_effective_period_ordered"),
        ),
        sa.CheckConstraint(
            "extraction_status IN ('pending', 'complete', 'incomplete', 'failed')",
            name=op.f("ck_source_versions_extraction_status"),
        ),
        sa.CheckConstraint(
            "length(btrim(parser_version)) > 0",
            name=op.f("ck_source_versions_parser_version_not_blank"),
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.id"],
            name="fk_source_versions_source_id_sources",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_source_versions"),
        sa.UniqueConstraint("source_id", "content_sha256", name="source_content_hash"),
    )
    op.create_index(
        "ix_source_versions_content_sha256",
        "source_versions",
        ["content_sha256"],
        unique=False,
    )

    op.create_table(
        "source_links",
        sa.Column("from_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("relation", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            "length(btrim(relation)) > 0",
            name=op.f("ck_source_links_relation_not_blank"),
        ),
        sa.ForeignKeyConstraint(
            ["from_version_id"],
            ["source_versions.id"],
            name="fk_source_links_from_version_id_source_versions",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["target_source_id"],
            ["sources.id"],
            name="fk_source_links_target_source_id_sources",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "from_version_id",
            "target_source_id",
            "relation",
            name="pk_source_links",
        ),
    )
    op.create_index(
        "ix_source_links_target_source_id",
        "source_links",
        ["target_source_id"],
        unique=False,
    )

    op.create_table(
        "applicability",
        sa.Column("version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("topic", sa.String(length=128), nullable=False),
        sa.Column("institution", sa.String(length=255), nullable=False),
        sa.Column("campus", sa.String(length=128), nullable=True),
        sa.Column("student_level", sa.String(length=128), nullable=True),
        sa.Column("program", sa.String(length=255), nullable=True),
        sa.Column("catalog_year", sa.String(length=32), nullable=True),
        sa.Column("term", sa.String(length=64), nullable=True),
        sa.Column("session", sa.String(length=64), nullable=True),
        sa.Column(
            "evidence_block_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            server_default=sa.text("'{}'::uuid[]"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "cardinality(evidence_block_ids) > 0",
            name=op.f("ck_applicability_has_evidence_blocks"),
        ),
        sa.CheckConstraint(
            "length(btrim(institution)) > 0",
            name=op.f("ck_applicability_institution_not_blank"),
        ),
        sa.CheckConstraint(
            "length(btrim(topic)) > 0",
            name=op.f("ck_applicability_topic_not_blank"),
        ),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["source_versions.id"],
            name="fk_applicability_version_id_source_versions",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("version_id", "topic", name="pk_applicability"),
    )
    op.create_index(
        "ix_applicability_topic_institution_version",
        "applicability",
        ["topic", "institution", "version_id"],
        unique=False,
    )
    op.create_index(
        "ix_applicability_academic_scope",
        "applicability",
        ["campus", "student_level", "program", "version_id"],
        unique=False,
    )
    op.create_index(
        "ix_applicability_term_scope",
        "applicability",
        ["term", "session", "catalog_year", "version_id"],
        unique=False,
    )

    op.create_table(
        "evidence_blocks",
        sa.Column("version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column(
            "heading_path",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("anchor", sa.String(length=512), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "structured_content",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("topic_key", sa.String(length=128), nullable=False),
        sa.Column(
            "scope",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.CheckConstraint(
            "ordinal >= 0",
            name=op.f("ck_evidence_blocks_ordinal_nonnegative"),
        ),
        sa.CheckConstraint(
            "page IS NULL OR page >= 1",
            name=op.f("ck_evidence_blocks_page_positive"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(scope) = 'object'",
            name=op.f("ck_evidence_blocks_scope_object"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(structured_content) IN ('object', 'array')",
            name=op.f("ck_evidence_blocks_structured_content_container"),
        ),
        sa.CheckConstraint(
            "length(btrim(text)) > 0",
            name=op.f("ck_evidence_blocks_text_not_blank"),
        ),
        sa.CheckConstraint(
            "length(btrim(topic_key)) > 0",
            name=op.f("ck_evidence_blocks_topic_key_not_blank"),
        ),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["source_versions.id"],
            name="fk_evidence_blocks_version_id_source_versions",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_evidence_blocks"),
        sa.UniqueConstraint("version_id", "ordinal", name="version_ordinal"),
    )
    op.create_index(
        "ix_evidence_blocks_topic_version",
        "evidence_blocks",
        ["topic_key", "version_id"],
        unique=False,
    )
    op.create_index(
        "ix_evidence_blocks_scope_gin",
        "evidence_blocks",
        ["scope"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"scope": "jsonb_path_ops"},
    )

    op.create_table(
        "qualifications",
        sa.Column("version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rule_version", sa.String(length=128), nullable=False),
        sa.Column(
            "checked_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=6), nullable=False),
        sa.Column(
            "check_results",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "provenance_evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "applicability_evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.CheckConstraint(
            "jsonb_typeof(applicability_evidence) = 'object'",
            name=op.f("ck_qualifications_applicability_evidence_object"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(check_results) = 'object'",
            name=op.f("ck_qualifications_check_results_object"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(provenance_evidence) = 'object'",
            name=op.f("ck_qualifications_provenance_evidence_object"),
        ),
        sa.CheckConstraint(
            "status IN ('passed', 'failed')",
            name=op.f("ck_qualifications_qualification_status"),
        ),
        sa.CheckConstraint(
            "length(btrim(rule_version)) > 0",
            name=op.f("ck_qualifications_rule_version_not_blank"),
        ),
        sa.CheckConstraint(
            "valid_until >= checked_at "
            "AND valid_until <= checked_at + INTERVAL '24 hours'",
            name=op.f("ck_qualifications_validity_within_24_hours"),
        ),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["source_versions.id"],
            name="fk_qualifications_version_id_source_versions",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_qualifications"),
        sa.UniqueConstraint(
            "version_id",
            "rule_version",
            "checked_at",
            name="version_rule_check",
        ),
    )
    op.create_index(
        "ix_qualifications_fresh_passed",
        "qualifications",
        ["version_id", "valid_until", "checked_at"],
        unique=False,
        postgresql_where=sa.text("status = 'passed'"),
    )

    op.create_table(
        "source_events",
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=13), nullable=False),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("rule_version", sa.String(length=128), nullable=False),
        sa.Column("reason_code", sa.String(length=128), nullable=False),
        sa.Column(
            "evidence_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            server_default=sa.text("'{}'::uuid[]"),
            nullable=False,
        ),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.CheckConstraint(
            "event_type IN ('qualification', 'change', 'withdrawal', 'restoration')",
            name=op.f("ck_source_events_source_event_type"),
        ),
        sa.CheckConstraint(
            "length(btrim(reason_code)) > 0",
            name=op.f("ck_source_events_reason_code_not_blank"),
        ),
        sa.CheckConstraint(
            "length(btrim(rule_version)) > 0",
            name=op.f("ck_source_events_rule_version_not_blank"),
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.id"],
            name="fk_source_events_source_id_sources",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_source_events"),
    )

    op.create_table(
        "ingestion_runs",
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=9), server_default="running", nullable=False),
        sa.Column("bounded_error_code", sa.String(length=128), nullable=True),
        sa.Column("version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.CheckConstraint(
            "completed_at IS NULL OR completed_at >= started_at",
            name=op.f("ck_ingestion_runs_completion_after_start"),
        ),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed')",
            name=op.f("ck_ingestion_runs_ingestion_status"),
        ),
        sa.CheckConstraint(
            "(status = 'running' AND completed_at IS NULL "
            "AND bounded_error_code IS NULL) OR "
            "(status = 'succeeded' AND completed_at IS NOT NULL "
            "AND bounded_error_code IS NULL) OR "
            "(status = 'failed' AND completed_at IS NOT NULL "
            "AND length(btrim(bounded_error_code)) > 0)",
            name=op.f("ck_ingestion_runs_status_completion_consistent"),
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.id"],
            name="fk_ingestion_runs_source_id_sources",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["source_versions.id"],
            name="fk_ingestion_runs_version_id_source_versions",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ingestion_runs"),
    )

    op.create_table(
        "conflict_evidence_blocks",
        sa.Column("conflict_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("evidence_block_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["conflict_id"],
            ["conflicts.id"],
            name="fk_conflict_evidence_blocks_conflict_id_conflicts",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_block_id"],
            ["evidence_blocks.id"],
            name="fk_conflict_evidence_blocks_evidence_block_id_evidence_blocks",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "conflict_id",
            "evidence_block_id",
            name="pk_conflict_evidence_blocks",
        ),
    )
    op.create_index(
        "ix_conflict_evidence_blocks_evidence_block_id",
        "conflict_evidence_blocks",
        ["evidence_block_id"],
        unique=False,
    )

    op.create_table(
        "course_relations",
        sa.Column("evidence_block_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("course_code", sa.String(length=32), nullable=False),
        sa.Column("related_course_code", sa.String(length=32), nullable=False),
        sa.Column("relation", sa.String(length=12), nullable=False),
        sa.Column(
            "group_expression",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("catalog_year", sa.String(length=32), nullable=True),
        sa.Column("campus", sa.String(length=128), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.CheckConstraint(
            "length(btrim(course_code)) > 0",
            name=op.f("ck_course_relations_course_code_not_blank"),
        ),
        sa.CheckConstraint(
            "relation IN ('prerequisite', 'corequisite', 'equivalent')",
            name=op.f("ck_course_relations_course_relation_type"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(group_expression) = 'object'",
            name=op.f("ck_course_relations_group_expression_object"),
        ),
        sa.CheckConstraint(
            "length(btrim(related_course_code)) > 0",
            name=op.f("ck_course_relations_related_course_code_not_blank"),
        ),
        sa.ForeignKeyConstraint(
            ["evidence_block_id"],
            ["evidence_blocks.id"],
            name="fk_course_relations_evidence_block_id_evidence_blocks",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_course_relations"),
    )
    op.create_index(
        "ix_course_relations_evidence_block_id",
        "course_relations",
        ["evidence_block_id"],
        unique=False,
    )

    op.create_table(
        "embeddings",
        sa.Column("block_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_revision", sa.String(length=255), nullable=False),
        sa.Column("dimensions", sa.Integer(), server_default="384", nullable=False),
        sa.Column("vector", VECTOR(dim=384), nullable=False),
        sa.CheckConstraint("dimensions = 384", name=op.f("ck_embeddings_dimensions_384")),
        sa.CheckConstraint(
            "length(btrim(model_revision)) > 0",
            name=op.f("ck_embeddings_model_revision_not_blank"),
        ),
        sa.ForeignKeyConstraint(
            ["block_id"],
            ["evidence_blocks.id"],
            name="fk_embeddings_block_id_evidence_blocks",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("block_id", "model_revision", name="pk_embeddings"),
    )
    # Exact vector search is required. This B-tree narrows the corpus by model revision before
    # PostgreSQL computes exact vector distance; no approximate HNSW/IVFFlat index is created.
    op.create_index(
        "ix_embeddings_model_revision_block",
        "embeddings",
        ["model_revision", "block_id"],
        unique=False,
    )

    op.create_table(
        "office_referrals",
        sa.Column("office_name", sa.String(length=255), nullable=False),
        sa.Column("responsibilities", sa.Text(), nullable=False),
        sa.Column("contact_label", sa.String(length=255), nullable=False),
        sa.Column("contact_url", sa.String(length=2048), nullable=False),
        sa.Column("evidence_block_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "scope",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.CheckConstraint(
            "length(btrim(contact_label)) > 0",
            name=op.f("ck_office_referrals_contact_label_not_blank"),
        ),
        sa.CheckConstraint(
            "contact_url ~ '^(https://|mailto:|tel:)'",
            name=op.f("ck_office_referrals_contact_url_supported_scheme"),
        ),
        sa.CheckConstraint(
            "length(btrim(office_name)) > 0",
            name=op.f("ck_office_referrals_office_name_not_blank"),
        ),
        sa.CheckConstraint(
            "length(btrim(responsibilities)) > 0",
            name=op.f("ck_office_referrals_responsibilities_not_blank"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(scope) = 'object'",
            name=op.f("ck_office_referrals_scope_object"),
        ),
        sa.ForeignKeyConstraint(
            ["evidence_block_id"],
            ["evidence_blocks.id"],
            name="fk_office_referrals_evidence_block_id_evidence_blocks",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_office_referrals"),
    )
    op.create_index(
        "ix_office_referrals_evidence_block_id",
        "office_referrals",
        ["evidence_block_id"],
        unique=False,
    )


def downgrade() -> None:
    """Remove the source schema while leaving the pgvector extension installed."""

    op.drop_table("office_referrals")
    op.drop_table("embeddings")
    op.drop_table("course_relations")
    op.drop_table("conflict_evidence_blocks")
    op.drop_table("ingestion_runs")
    op.drop_table("source_events")
    op.drop_table("qualifications")
    op.drop_table("evidence_blocks")
    op.drop_table("applicability")
    op.drop_table("source_links")
    op.drop_table("source_versions")
    op.drop_table("sources")
    op.drop_table("conflicts")
    op.drop_table("aggregate_metrics")
