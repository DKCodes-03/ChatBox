"""Enable the pgvector extension.

Revision ID: 001_enable_pgvector
Revises: None
"""

from collections.abc import Sequence

from alembic import op

revision: str = "001_enable_pgvector"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Install pgvector in the application database."""

    op.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")


def downgrade() -> None:
    """Remove pgvector after dependent schema revisions have been reversed."""

    op.execute("DROP EXTENSION IF EXISTS vector")
