"""Credential and service boundaries for migration, runtime, and governance roles."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from app.config import DatabaseRole, Settings

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_PATH = REPOSITORY_ROOT / "deploy/compose.yaml"
ROLE_PROVISION_PATH = REPOSITORY_ROOT / "deploy/provision-database-roles.sh"


def _secret(path: Path, value: str) -> Path:
    path.write_text(value, encoding="utf-8")
    return path


def _service_block(compose: str, service: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(service)}:\n(?P<body>.*?)(?=^  [a-z][a-z0-9_-]*:\n|\Z)",
        compose,
    )
    assert match is not None, f"Compose service {service} is missing"
    return match.group("body")


def test_each_database_role_uses_its_own_user_and_secret(tmp_path: Path) -> None:
    runtime_password = _secret(tmp_path / "runtime", "runtime-test-password")
    migration_password = _secret(tmp_path / "migration", "migration-test-password")
    governance_password = _secret(tmp_path / "governance", "governance-test-password")
    settings = Settings(
        postgres_host="postgres.test",
        postgres_runtime_user="runtime_role",
        postgres_migration_user="migration_role",
        postgres_governance_user="governance_role",
        postgres_runtime_password_file=runtime_password,
        postgres_migration_password_file=migration_password,
        postgres_governance_password_file=governance_password,
    )

    urls = {role: settings.database_url(role) for role in DatabaseRole}

    assert {url.username for url in urls.values()} == {
        "runtime_role",
        "migration_role",
        "governance_role",
    }
    assert {url.password for url in urls.values()} == {
        "runtime-test-password",
        "migration-test-password",
        "governance-test-password",
    }
    assert all(url.host == "postgres.test" for url in urls.values())
    assert all(url.database == "pnw_chatbot" for url in urls.values())
    for password in (
        "runtime-test-password",
        "migration-test-password",
        "governance-test-password",
    ):
        assert all(password not in str(url) for url in urls.values())


def test_default_database_url_is_always_the_restricted_runtime_role(tmp_path: Path) -> None:
    runtime_password = _secret(tmp_path / "runtime", "runtime-test-password")
    migration_password = _secret(tmp_path / "migration", "migration-test-password")
    governance_password = _secret(tmp_path / "governance", "governance-test-password")
    settings = Settings(
        postgres_runtime_password_file=runtime_password,
        postgres_migration_password_file=migration_password,
        postgres_governance_password_file=governance_password,
    )

    default_url = settings.database_url()

    assert default_url.username == settings.postgres_runtime_user
    assert default_url.password == "runtime-test-password"
    assert default_url.username != settings.postgres_migration_user
    assert default_url.username != settings.postgres_governance_user


def test_database_role_names_must_be_distinct() -> None:
    with pytest.raises(ValueError, match="must be distinct"):
        Settings(
            postgres_runtime_user="shared_role",
            postgres_migration_user="shared_role",
        )


def test_compose_mounts_only_the_role_secret_needed_by_each_service() -> None:
    compose = COMPOSE_PATH.read_text(encoding="utf-8")
    database = _service_block(compose, "db")
    migration = _service_block(compose, "migrate")
    provisioner = _service_block(compose, "provision-db-roles")
    api = _service_block(compose, "api")
    ingestion = _service_block(compose, "ingest")

    assert "/run/secrets/postgres_migration_password" in database
    assert "postgres_runtime_password" not in database
    assert "postgres_governance_password" not in database

    assert "/run/secrets/postgres_migration_password" in migration
    assert "postgres_runtime_password" not in migration
    assert "postgres_governance_password" not in migration

    assert "      - postgres_migration_password" in provisioner
    assert "      - postgres_runtime_password" in provisioner
    assert "      - postgres_governance_password" in provisioner

    assert "/run/secrets/postgres_runtime_password" in api
    assert "postgres_migration_password" not in api
    assert "postgres_governance_password" not in api

    assert "/run/secrets/postgres_governance_password" in ingestion
    assert "postgres_runtime_password" not in ingestion
    assert "postgres_migration_password" not in ingestion


def test_role_provisioner_applies_fixed_least_privilege_grants() -> None:
    script = ROLE_PROVISION_PATH.read_text(encoding="utf-8")

    assert "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS" in script
    assert "REVOKE CREATE ON SCHEMA public FROM PUBLIC" in script
    assert "GRANT SELECT, INSERT, UPDATE ON aggregate_metrics" in script
    assert "source_links" in script
    assert (
        'GRANT EXECUTE ON FUNCTION public.lock_sources_for_answer(uuid[]) TO :"runtime_role"'
        in script
    )
    assert (
        'GRANT EXECUTE ON FUNCTION public.lock_sources_for_answer(uuid[]) TO :"governance_role"'
        in script
    )
    assert "GRANT CONNECT ON DATABASE" in script
    assert "GRANT CREATE" not in script
    assert "GRANT ALL" not in script


def test_database_has_no_published_host_port() -> None:
    compose = COMPOSE_PATH.read_text(encoding="utf-8")
    database = _service_block(compose, "db")

    assert re.search(r"(?m)^    ports:\s*$", database) is None
    assert re.search(r"(?m)^    networks:\s*\n      - backend\s*$", database) is not None
