"""Alembic runtime configuration using file-mounted migration credentials."""

from __future__ import annotations

import os
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from app.models import Base
from sqlalchemy import engine_from_config, pool
from sqlalchemy.engine import URL

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _required_environment(name: str) -> str:
    """Return a required nonsecret setting without inventing deployment defaults."""

    value = os.environ.get(name)
    if value is None or value == "":
        raise RuntimeError(f"Required migration setting {name} is not configured")
    return value


def _migration_password() -> str:
    """Read the migration password without placing it in environment variables."""

    secret_path = os.environ.get("POSTGRES_PASSWORD_FILE") or os.environ.get(
        "POSTGRES_MIGRATION_PASSWORD_FILE"
    )
    if not secret_path:
        raise RuntimeError("A migration password file is not configured")

    try:
        password = Path(secret_path).read_text(encoding="utf-8").rstrip("\r\n")
    except OSError as exc:
        raise RuntimeError("The configured migration password file cannot be read") from exc

    if password == "":
        raise RuntimeError("The configured migration password file is empty")
    return password


def _database_url() -> str:
    """Build a psycopg SQLAlchemy URL from Compose settings and a mounted secret."""

    user = os.environ.get("POSTGRES_MIGRATION_USER") or os.environ.get("POSTGRES_USER")
    if not user:
        raise RuntimeError("Required migration setting POSTGRES_MIGRATION_USER is not configured")

    try:
        port = int(_required_environment("POSTGRES_PORT"))
    except ValueError as exc:
        raise RuntimeError("POSTGRES_PORT must be an integer") from exc

    url = URL.create(
        drivername="postgresql+psycopg",
        username=user,
        password=_migration_password(),
        host=_required_environment("POSTGRES_HOST"),
        port=port,
        database=_required_environment("POSTGRES_DB"),
    )
    return url.render_as_string(hide_password=False)


def _configure_database_url() -> None:
    # ConfigParser treats percent signs as interpolation markers. URL escaping can introduce them,
    # so double each sign while storing the value; Alembic resolves it back before connecting.
    config.set_main_option("sqlalchemy.url", _database_url().replace("%", "%%"))


def run_migrations_offline() -> None:
    """Render SQL without opening a database connection."""

    _configure_database_url()
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in a transaction against PostgreSQL."""

    _configure_database_url()
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
