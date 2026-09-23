from enum import Enum

from pydantic import AnyHttpUrl, BaseModel, Field


class SourceStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    DISPUTED = "disputed"


class SourceSearchRequest(BaseModel):
    q: str = Field(min_length=1)
    campus: str | None = Field(default=None, min_length=1)
    term: str | None = Field(default=None, min_length=1)


class SourceSummary(BaseModel):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    url: AnyHttpUrl
    status: SourceStatus


class SourceSearchResponse(BaseModel):
    results: list[SourceSummary] = Field(default_factory=list)