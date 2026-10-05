"""Strict public schemas for the session and message HTTP contract."""

from __future__ import annotations

from typing import Annotated, Self
from uuid import UUID

from pydantic import (
    AnyUrl,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    UrlConstraints,
    model_validator,
)

from app.api.errors import SafeErrorCode
from app.context import ContextField, StudentContext
from app.generation.schemas import AnswerOutcome, ReasonCode, SegmentKind

MAX_MESSAGE_CHARACTERS = 4_000
MAX_CITATIONS = 8

NonBlankText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
MessageText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_MESSAGE_CHARACTERS),
]
SourceUrl = Annotated[AnyUrl, UrlConstraints(allowed_schemes=["https"], max_length=2_048)]
ContactUrl = Annotated[
    AnyUrl,
    UrlConstraints(allowed_schemes=["https", "mailto", "tel"], max_length=2_048),
]


def _is_none(value: object) -> bool:
    return value is None


class APIModel(BaseModel):
    """Reject contract drift and keep request content out of validation representations."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        str_strip_whitespace=True,
    )


class SessionCreateRequest(APIModel):
    """The session creation endpoint accepts exactly an empty JSON object."""


class SessionResponse(APIModel):
    """Non-content session metadata returned on creation and inspection."""

    expires_at: AwareDatetime
    server_time: AwareDatetime


class Context(StudentContext):
    """Explicit answer context; omitted values remain unknown."""


class MessageRequest(APIModel):
    """A bounded student message and optional explicit context."""

    message: MessageText = Field(repr=False)
    context: Context | None = None


class AnswerSegment(APIModel):
    """One ordered answer segment with its public citation references."""

    kind: SegmentKind
    text: NonBlankText = Field(repr=False)
    citation_ids: tuple[NonBlankText, ...] = Field(max_length=MAX_CITATIONS)

    @model_validator(mode="after")
    def _validate_citation_ids(self) -> Self:
        if len(self.citation_ids) != len(set(self.citation_ids)):
            raise ValueError("segment citation IDs must be unique")
        if self.kind in (SegmentKind.EXPLANATION, SegmentKind.STEP) and not self.citation_ids:
            raise ValueError("supported segments require citations")
        return self


class Citation(APIModel):
    """Public source metadata derived from eligible evidence."""

    id: NonBlankText
    source_title: NonBlankText
    url: SourceUrl
    section: NonBlankText | None = Field(default=None, exclude_if=_is_none)
    page: int | None = Field(default=None, ge=1, exclude_if=_is_none)
    version_id: UUID


class Clarification(APIModel):
    """A focused request for missing explicit context."""

    question: NonBlankText = Field(repr=False)
    fields: tuple[ContextField, ...] = Field(min_length=1, max_length=len(ContextField))
    options: tuple[NonBlankText, ...] | None = Field(default=None, exclude_if=_is_none)

    @model_validator(mode="after")
    def _validate_unique_values(self) -> Self:
        if len(self.fields) != len(set(self.fields)):
            raise ValueError("clarification fields must be unique")
        if self.options is not None and len(self.options) != len(set(self.options)):
            raise ValueError("clarification options must be unique")
        return self


class Referral(APIModel):
    """An evidence-backed office contact for an unable response."""

    office_name: NonBlankText
    contact_label: NonBlankText
    contact_url: ContactUrl
    citation_ids: tuple[NonBlankText, ...] = Field(min_length=1, max_length=MAX_CITATIONS)

    @model_validator(mode="after")
    def _validate_citation_ids(self) -> Self:
        if len(self.citation_ids) != len(set(self.citation_ids)):
            raise ValueError("referral citation IDs must be unique")
        return self


class AnswerEnvelope(APIModel):
    """A complete validated response; no provider or evidence internals are exposed."""

    outcome: AnswerOutcome
    segments: tuple[AnswerSegment, ...]
    citations: tuple[Citation, ...]
    context: Context
    clarification: Clarification | None
    referral: Referral | None
    reason_code: ReasonCode | None
    expires_at: AwareDatetime
    server_time: AwareDatetime

    @model_validator(mode="after")
    def _validate_outcome_and_references(self) -> Self:
        supported = tuple(
            segment
            for segment in self.segments
            if segment.kind in (SegmentKind.EXPLANATION, SegmentKind.STEP)
        )
        limitations = tuple(
            segment for segment in self.segments if segment.kind is SegmentKind.LIMITATION
        )

        if self.outcome is AnswerOutcome.ANSWER:
            if (
                not supported
                or limitations
                or self.clarification is not None
                or self.referral is not None
                or self.reason_code is not None
            ):
                raise ValueError("answer outcome has an invalid structure")
        elif self.outcome is AnswerOutcome.PARTIAL:
            if (
                not supported
                or not limitations
                or self.clarification is not None
                or self.referral is not None
                or self.reason_code is None
            ):
                raise ValueError("partial outcome has an invalid structure")
        elif self.outcome is AnswerOutcome.CLARIFICATION:
            if (
                self.segments
                or self.citations
                or self.clarification is None
                or self.referral is not None
                or self.reason_code is not ReasonCode.CONTEXT_REQUIRED
            ):
                raise ValueError("clarification outcome has an invalid structure")
        elif (
            not limitations
            or supported
            or self.clarification is not None
            or self.reason_code is None
        ):
            raise ValueError("unable outcome has an invalid structure")

        citation_ids = tuple(citation.id for citation in self.citations)
        if len(citation_ids) != len(set(citation_ids)):
            raise ValueError("citation IDs must be unique")
        known_citation_ids = set(citation_ids)
        referenced_ids = {
            citation_id for segment in self.segments for citation_id in segment.citation_ids
        }
        if self.referral is not None:
            referenced_ids.update(self.referral.citation_ids)
        if referenced_ids != known_citation_ids:
            raise ValueError("citation references must exactly match the citation objects")
        return self


class ErrorDetail(APIModel):
    """A fixed safe error without submitted or dependency content."""

    code: SafeErrorCode
    message: NonBlankText
    retryable: bool


class ErrorEnvelope(APIModel):
    """The shared error response shape."""

    error: ErrorDetail
    server_time: AwareDatetime


# Endpoint-oriented aliases keep the schema names clear at call sites.
CreateSessionRequest = SessionCreateRequest
SessionEnvelope = SessionResponse

__all__ = [
    "AnswerEnvelope",
    "AnswerOutcome",
    "AnswerSegment",
    "Citation",
    "Clarification",
    "Context",
    "ContextField",
    "CreateSessionRequest",
    "ErrorDetail",
    "ErrorEnvelope",
    "MessageRequest",
    "ReasonCode",
    "Referral",
    "SegmentKind",
    "SessionCreateRequest",
    "SessionEnvelope",
    "SessionResponse",
]
