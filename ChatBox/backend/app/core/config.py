from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "PNW Information Chatbot"
    environment: str = "development"
    database_url: str = Field(
        default="postgresql+asyncpg://pnw_chatbot:change-me-for-local-development@localhost:5432/pnw_chatbot"
    )
    ollama_base_url: str = "http://localhost:11434"
    embedding_model_name: str = Field(default="nomic-embed-text", min_length=1)
    embedding_model_version: str = Field(default="unspecified", min_length=1)
    embedding_dimensions: int = Field(default=768, ge=1)
    embedding_timeout: float = Field(default=30.0, gt=0)
    database_pool_size: int = Field(default=5, ge=1)
    database_max_overflow: int = Field(default=10, ge=0)
    database_pool_timeout: float = Field(default=30.0, gt=0)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()