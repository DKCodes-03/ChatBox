"""Provider-neutral schemas for structured, evidence-referencing answer candidates."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Self
from uuid import UUID

from app.context import ContextField
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

NonBlankText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
MAX_EVIDENCE_REFERENCES = 8


class AnswerOutcome(StrEnum):
    ANSWER = "answer"
    PARTIAL = "partial"
    CLARIFICATION = "clarification"
    UNABLE = "unable"


class SegmentKind(StrEnum):
    EXPLANATION = "explanation"
    STEP = "step"
    LIMITATION = "limitation"


class ReasonCode(StrEnum):
    MISSING_EVIDENCE = "missing_evidence"
    CONFLICT = "conflict"
    EXPIRED_SOURCE = "expired_source"
    CONTEXT_REQUIRED = "context_required"
    PERSONAL_CASE = "personal_case"
    OUT_OF_SCOPE = "out_of_scope"
    SOURCE_UNAVAILABLE = "source_unavailable"
    PROCESSING_UNAVAILABLE = "processing_unavailable"


class StructuredModel(BaseModel):
    """Strict base configuration shared by all provider-output models."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        str_strip_whitespace=True,
    )


class AnswerSegment(StructuredModel):
    """One candidate segment tied directly to eligible evidence identifiers."""

    kind: SegmentKind
    text: NonBlankText = Field(repr=False)
    evidence_ids: tuple[UUID, ...] = Field(
        default_factory=tuple,
        max_length=MAX_EVIDENCE_REFERENCES,
    )

    @model_validator(mode="after")
    def _validate_evidence_references(self) -> Self:
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("evidence references must be unique")
        if self.kind in (SegmentKind.EXPLANATION, SegmentKind.STEP) and not self.evidence_ids:
            raise ValueError("supported segments require evidence references")
        return self


class ClarificationRequest(StructuredModel):
    """A focused request for explicit context rather than a guessed answer."""

    question: NonBlankText = Field(repr=False)
    fields: tuple[ContextField, ...] = Field(min_length=1)
    options: tuple[NonBlankText, ...] = Field(default_factory=tuple, repr=False)

    @model_validator(mode="after")
    def _validate_unique_values(self) -> Self:
        if len(self.fields) != len(set(self.fields)):
            raise ValueError("clarification fields must be unique")
        if len(self.options) != len(set(self.options)):
            raise ValueError("clarification options must be unique")
        return self


class StructuredAnswer(StructuredModel):
    """An untrusted candidate that must pass evidence validation before release."""

    outcome: AnswerOutcome
    segments: tuple[AnswerSegment, ...] = Field(default_factory=tuple)
    clarification: ClarificationRequest | None = None
    reason_code: ReasonCode | None = None

    @model_validator(mode="after")
    def _validate_outcome_shape(self) -> Self:
        supported_segments = tuple(
            segment
            for segment in self.segments
            if segment.kind in (SegmentKind.EXPLANATION, SegmentKind.STEP)
        )
        limitation_segments = tuple(
            segment for segment in self.segments if segment.kind is SegmentKind.LIMITATION
        )

        if self.outcome is AnswerOutcome.ANSWER:
            if (
                not supported_segments
                or limitation_segments
                or self.clarification is not None
                or self.reason_code is not None
            ):
                raise ValueError("answer outcome has an invalid structure")
        elif self.outcome is AnswerOutcome.PARTIAL:
            if (
                not supported_segments
                or not limitation_segments
                or self.clarification is not None
                or self.reason_code is None
            ):
                raise ValueError("partial outcome has an invalid structure")
        elif self.outcome is AnswerOutcome.CLARIFICATION:
            if (
                self.segments
                or self.clarification is None
                or self.reason_code is not ReasonCode.CONTEXT_REQUIRED
            ):
                raise ValueError("clarification outcome has an invalid structure")
        elif (
            not limitation_segments
            or supported_segments
            or self.clarification is not None
            or self.reason_code is None
        ):
            raise ValueError("unable outcome has an invalid structure")
        return self
