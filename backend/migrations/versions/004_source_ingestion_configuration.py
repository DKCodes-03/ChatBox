"""Persist bounded source-manifest configuration.

Revision ID: 004_source_ingestion_config
Revises: 003_answer_source_locks
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "004_source_ingestion_config"
down_revision: str | None = "003_answer_source_locks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Store the operator-supplied topic and child-host bounds used by later fetches."""

    op.add_column(
        "sources",
        sa.Column(
            "configured_topics",
            postgresql.ARRAY(sa.String(length=128)),
            server_default=sa.text("'{}'::varchar[]"),
            nullable=False,
        ),
    )
    op.add_column(
        "sources",
        sa.Column(
            "allowed_child_hosts",
            postgresql.ARRAY(sa.String(length=255)),
            server_default=sa.text("'{}'::varchar[]"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        op.f("ck_sources_configured_topics_bounded"),
        "sources",
        "cardinality(configured_topics) BETWEEN 0 AND 16",
    )
    op.create_check_constraint(
        op.f("ck_sources_allowed_child_hosts_bounded"),
        "sources",
        "cardinality(allowed_child_hosts) BETWEEN 0 AND 3",
    )


def downgrade() -> None:
    """Remove persisted source-manifest configuration."""

    op.drop_constraint(
        op.f("ck_sources_allowed_child_hosts_bounded"),
        "sources",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_sources_configured_topics_bounded"),
        "sources",
        type_="check",
    )
    op.drop_column("sources", "allowed_child_hosts")
    op.drop_column("sources", "configured_topics")
