"""Add a least-privilege source lock used during answer publication.

Revision ID: 003_answer_source_locks
Revises: 002_source_schema
"""

from collections.abc import Sequence

from alembic import op

revision: str = "003_answer_source_locks"
down_revision: str | None = "002_source_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create a narrowly scoped definer function for shared source-row locks."""

    op.execute(
        """
        CREATE FUNCTION public.lock_sources_for_answer(source_ids uuid[])
        RETURNS bigint
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        DECLARE
            locked_count bigint;
        BEGIN
            SELECT count(*)
            INTO locked_count
            FROM (
                SELECT source.id
                FROM public.sources AS source
                WHERE source.id = ANY(source_ids)
                ORDER BY source.id
                FOR SHARE
            ) AS locked_sources;
            RETURN locked_count;
        END;
        $$
        """
    )
    op.execute(
        "REVOKE ALL ON FUNCTION public.lock_sources_for_answer(uuid[]) FROM PUBLIC"
    )


def downgrade() -> None:
    """Remove the final-answer source lock function."""

    op.execute("DROP FUNCTION public.lock_sources_for_answer(uuid[])")
