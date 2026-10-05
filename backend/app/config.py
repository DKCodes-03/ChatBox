"""Typed, fail-closed application configuration loaded from environment variables."""

from __future__ import annotations

from datetime import timedelta
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy.engine import URL

MAX_DOCUMENT_BYTES = 20 * 1024 * 1024
APPROVED_SOURCE_HOSTS = frozenset({"pnw.edu", "www.pnw.edu", "catalog.pnw.edu"})

CsvTuple = Annotated[tuple[str, ...], NoDecode]


class AppEnvironment(StrEnum):
    """Supported deployment environments."""

    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class ProviderMode(StrEnum):
    """Generation providers permitted by the approved architecture."""

    GEMINI = "gemini"
    LOCAL = "local"


class DatabaseRole(StrEnum):
    """Database credential sets mounted into isolated services."""

    RUNTIME = "runtime"
    MIGRATION = "migration"
    GOVERNANCE = "governance"


class SecretFileError(RuntimeError):
    """A configured secret file is unavailable or empty."""


class Settings(BaseSettings):
    """Validated settings for API, retrieval, generation, and source ingestion."""

    model_config = SettingsConfigDict(
        case_sensitive=False,
        env_ignore_empty=True,
        env_prefix="",
        extra="ignore",
        frozen=True,
        validate_default=True,
    )

    app_env: AppEnvironment = AppEnvironment.DEVELOPMENT
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    public_origin: AnyHttpUrl = AnyHttpUrl("http://localhost:8080")
    api_workers: int = Field(default=1, ge=1, le=1)

    chat_access_log_enabled: bool = False
    request_body_logging_enabled: bool = False
    sql_echo: bool = False
    trace_content_enabled: bool = False
    synthetic_test_mode: bool = False

    postgres_host: str = Field(default="db", min_length=1, max_length=253)
    postgres_port: int = Field(default=5432, ge=1, le=65535)
    postgres_db: str = Field(default="pnw_chatbot", min_length=1, max_length=63)
    postgres_runtime_user: str = Field(default="pnw_chatbot_api", min_length=1, max_length=63)
    postgres_migration_user: str = Field(
        default="pnw_chatbot_migrator",
        min_length=1,
        max_length=63,
    )
    postgres_governance_user: str = Field(
        default="pnw_chatbot_governance",
        min_length=1,
        max_length=63,
    )
    postgres_runtime_password_file: Path = Path("/run/secrets/postgres_runtime_password")
    postgres_migration_password_file: Path = Path("/run/secrets/postgres_migration_password")
    postgres_governance_password_file: Path = Path("/run/secrets/postgres_governance_password")

    llm_provider: ProviderMode = ProviderMode.GEMINI
    gemini_model: Literal["gemini-3.5-flash-lite"] = "gemini-3.5-flash-lite"
    gemini_api_key_file: Path = Path("/run/secrets/gemini_api_key")
    gemini_max_output_tokens: int = Field(default=512, ge=1, le=512)
    generation_timeout_seconds: float = Field(default=9, gt=0, le=9)
    generation_max_concurrency: int = Field(default=4, ge=1, le=4)
    gemini_production_data_handling_approved: bool = False
    provider_conversation_ids_enabled: bool = False
    provider_prompt_cache_enabled: bool = False

    local_inference_enabled: bool = False
    local_inference_base_url: AnyHttpUrl = AnyHttpUrl("http://inference:8080")
    local_inference_model_path: Path = Path("/models/qwen3-4b-q4_k_m.gguf")

    embedding_model: Literal["sentence-transformers/all-MiniLM-L6-v2"] = (
        "sentence-transformers/all-MiniLM-L6-v2"
    )
    embedding_model_cache_dir: Path = Path("/models/embeddings")
    embedding_dimensions: int = Field(default=384, ge=384, le=384)
    evidence_max_wordpieces: int = Field(default=220, ge=1, le=220)
    retrieval_max_evidence_blocks: int = Field(default=8, ge=1, le=8)

    source_allowed_schemes: CsvTuple = ("https",)
    source_allowed_hosts: CsvTuple = ("pnw.edu", "www.pnw.edu", "catalog.pnw.edu")
    source_max_crawl_depth: int = Field(default=3, ge=0, le=3)
    source_max_urls_per_run: int = Field(default=100, ge=1, le=100)
    source_max_document_bytes: int = Field(default=MAX_DOCUMENT_BYTES, ge=1, le=MAX_DOCUMENT_BYTES)
    source_refresh_interval_hours: int = Field(default=24, ge=24, le=24)
    qualification_max_age_hours: int = Field(default=24, ge=24, le=24)
    max_evidence_blocks: int = Field(default=50_000, ge=1, le=50_000)

    session_idle_seconds: int = Field(default=1800, ge=1800, le=1800)
    session_warning_seconds: int = Field(default=1680, ge=1680, le=1680)
    session_messages_per_minute: int = Field(default=6, ge=1, le=6)
    max_active_sessions: int = Field(default=200, ge=1, le=200)
    max_request_body_bytes: int = Field(default=8192, ge=1, le=8192)
    max_question_characters: int = Field(default=4000, ge=1, le=4000)

    @field_validator("source_allowed_schemes", "source_allowed_hosts", mode="before")
    @classmethod
    def _parse_csv_tuple(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        return tuple(item.strip() for item in value.split(",") if item.strip())

    @field_validator("source_allowed_schemes")
    @classmethod
    def _validate_source_schemes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip().lower() for item in value)
        if normalized != ("https",):
            raise ValueError("source discovery permits only the HTTPS scheme")
        return normalized

    @field_validator("source_allowed_hosts")
    @classmethod
    def _validate_source_hosts(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip().lower().rstrip(".") for item in value)
        if not normalized:
            raise ValueError("at least one approved source host is required")
        if len(normalized) != len(set(normalized)):
            raise ValueError("source hosts must not contain duplicates")
        unsupported = set(normalized) - APPROVED_SOURCE_HOSTS
        if unsupported:
            raise ValueError("source hosts must remain within the approved PNW boundary")
        return normalized

    @field_validator(
        "postgres_runtime_password_file",
        "postgres_migration_password_file",
        "postgres_governance_password_file",
        "gemini_api_key_file",
        "local_inference_model_path",
        "embedding_model_cache_dir",
    )
    @classmethod
    def _require_absolute_container_path(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("container file paths must be absolute")
        return value

    @model_validator(mode="after")
    def _validate_provider_policy(self) -> Self:
        database_users = {
            self.postgres_runtime_user,
            self.postgres_migration_user,
            self.postgres_governance_user,
        }
        if len(database_users) != 3:
            raise ValueError("runtime, migration, and governance database users must be distinct")
        if (
            self.public_origin.path not in (None, "", "/")
            or self.public_origin.query is not None
            or self.public_origin.fragment is not None
            or self.public_origin.username is not None
            or self.public_origin.password is not None
        ):
            raise ValueError("public origin must contain only scheme, host, and optional port")
        if any(
            (
                self.chat_access_log_enabled,
                self.request_body_logging_enabled,
                self.sql_echo,
                self.trace_content_enabled,
            )
        ):
            raise ValueError("content-bearing logs, SQL echo, and tracing must remain disabled")
        if self.provider_conversation_ids_enabled or self.provider_prompt_cache_enabled:
            raise ValueError("provider conversation IDs and prompt caching must remain disabled")
        if self.synthetic_test_mode and self.app_env is not AppEnvironment.TEST:
            raise ValueError("synthetic fixture mode is permitted only in the test environment")
        if self.llm_provider is ProviderMode.LOCAL and not self.local_inference_enabled:
            raise ValueError("local provider mode requires local inference to be enabled")
        if (
            self.app_env is AppEnvironment.PRODUCTION
            and self.llm_provider is ProviderMode.GEMINI
            and not self.gemini_production_data_handling_approved
        ):
            raise ValueError("production Gemini use requires explicit data-handling approval")
        if self.app_env is AppEnvironment.PRODUCTION and self.public_origin.scheme != "https":
            raise ValueError("production public origin must use HTTPS")
        return self

    @staticmethod
    def _read_secret(path: Path, label: str) -> SecretStr:
        try:
            value = path.read_text(encoding="utf-8").rstrip("\r\n")
        except OSError as exc:
            raise SecretFileError(f"{label} secret file is unavailable") from exc
        if not value:
            raise SecretFileError(f"{label} secret file is empty")
        if "\x00" in value:
            raise SecretFileError(f"{label} secret file is invalid")
        return SecretStr(value)

    def gemini_api_key(self) -> SecretStr:
        """Read the Gemini key only when a provider call needs it."""

        return self._read_secret(self.gemini_api_key_file, "Gemini API key")

    @property
    def qualification_max_age(self) -> timedelta:
        """Return the fixed qualification freshness boundary as a duration."""

        return timedelta(hours=self.qualification_max_age_hours)

    @property
    def source_refresh_interval(self) -> timedelta:
        """Return the required source recheck interval as a duration."""

        return timedelta(hours=self.source_refresh_interval_hours)

    def database_password(self, role: DatabaseRole = DatabaseRole.RUNTIME) -> SecretStr:
        """Read the password mounted for the selected isolated database role."""

        paths = {
            DatabaseRole.RUNTIME: self.postgres_runtime_password_file,
            DatabaseRole.MIGRATION: self.postgres_migration_password_file,
            DatabaseRole.GOVERNANCE: self.postgres_governance_password_file,
        }
        return self._read_secret(paths[role], f"{role.value} database password")

    def database_url(self, role: DatabaseRole = DatabaseRole.RUNTIME) -> URL:
        """Build a safely escaped psycopg URL without exposing its password in repr output."""

        users = {
            DatabaseRole.RUNTIME: self.postgres_runtime_user,
            DatabaseRole.MIGRATION: self.postgres_migration_user,
            DatabaseRole.GOVERNANCE: self.postgres_governance_user,
        }
        password = self.database_password(role).get_secret_value()
        return URL.create(
            drivername="postgresql+psycopg",
            username=users[role],
            password=password,
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return one validated immutable settings object per process."""

    return Settings()
