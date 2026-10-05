"""Canonical models for explicit, nonpersonal answer context."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from enum import StrEnum
from typing import Annotated, Self, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
)

MAX_CONTEXT_VALUE_CHARACTERS = 255

ContextValue = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=MAX_CONTEXT_VALUE_CHARACTERS,
    ),
]

_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def _is_none(value: object) -> bool:
    return value is None


class ContextField(StrEnum):
    """Context dimensions that may be supplied explicitly by a student."""

    CAMPUS = "campus"
    TERM = "term"
    YEAR = "year"
    SESSION = "session"
    PROGRAM = "program"
    STUDENT_LEVEL = "student_level"
    CATALOG_YEAR = "catalog_year"


class ContextValidationError(ValueError):
    """A sanitized context error that never includes a submitted value."""

    def __init__(self, message: str = "context is invalid") -> None:
        super().__init__(message)


class UnsupportedContextValueError(ContextValidationError):
    """One or more explicit values are absent from eligible source metadata."""

    def __init__(self, fields: Iterable[ContextField]) -> None:
        self.fields = tuple(dict.fromkeys(fields))
        super().__init__("context value is not supported by eligible metadata")


class ContextModel(BaseModel):
    """Strict base configuration shared by context selections and option sets."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        str_strip_whitespace=True,
    )


class StudentContext(ContextModel):
    """Explicit context retained only for the active in-memory conversation."""

    campus: ContextValue | None = Field(default=None, exclude_if=_is_none)
    term: ContextValue | None = Field(default=None, exclude_if=_is_none)
    year: ContextValue | None = Field(default=None, exclude_if=_is_none)
    session: ContextValue | None = Field(default=None, exclude_if=_is_none)
    program: ContextValue | None = Field(default=None, exclude_if=_is_none)
    student_level: ContextValue | None = Field(default=None, exclude_if=_is_none)
    catalog_year: ContextValue | None = Field(default=None, exclude_if=_is_none)

    @field_validator("*")
    @classmethod
    def _reject_control_characters(cls, value: str | None) -> str | None:
        if value is not None and _CONTROL_RE.search(value):
            raise ValueError("context values cannot contain control characters")
        return value

    def as_mapping(self) -> dict[str, str]:
        """Return only explicitly supplied values using public contract field names."""

        return self.model_dump(exclude_none=True)

    def as_field_mapping(self) -> dict[ContextField, str]:
        """Return explicitly supplied values keyed by the canonical field enum."""

        return {ContextField(key): value for key, value in self.as_mapping().items()}

    def missing(self, required_fields: Iterable[ContextField]) -> tuple[ContextField, ...]:
        """Return required fields that remain unknown, preserving requested order."""

        ordered_fields = tuple(dict.fromkeys(required_fields))
        if any(not isinstance(field, ContextField) for field in ordered_fields):
            raise ContextValidationError()
        values = self.as_field_mapping()
        return tuple(field for field in ordered_fields if field not in values)

    def validate_against(self, options: ContextOptions) -> Self:
        """Require explicit values to match options derived from eligible metadata.

        An empty option set means that a caller did not provide metadata for that field. T039's
        context gate decides which fields require a nonempty option set for a particular query.
        """

        unsupported: list[ContextField] = []
        for field, value in self.as_field_mapping().items():
            eligible = options.for_field(field)
            if eligible and value not in eligible:
                unsupported.append(field)
        if unsupported:
            raise UnsupportedContextValueError(unsupported)
        return self


class ContextOptions(ContextModel):
    """Exact context labels extracted from eligible source metadata."""

    campus: tuple[ContextValue, ...] = Field(default_factory=tuple)
    term: tuple[ContextValue, ...] = Field(default_factory=tuple)
    year: tuple[ContextValue, ...] = Field(default_factory=tuple)
    session: tuple[ContextValue, ...] = Field(default_factory=tuple)
    program: tuple[ContextValue, ...] = Field(default_factory=tuple)
    student_level: tuple[ContextValue, ...] = Field(default_factory=tuple)
    catalog_year: tuple[ContextValue, ...] = Field(default_factory=tuple)

    @field_validator("*")
    @classmethod
    def _validate_options(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)) or any(_CONTROL_RE.search(value) for value in values):
            raise ValueError("context options must be unique and cannot contain control characters")
        return values

    def for_field(self, field: ContextField) -> tuple[str, ...]:
        if not isinstance(field, ContextField):
            raise ContextValidationError()
        return cast(tuple[str, ...], getattr(self, field.value))


def validate_context(value: StudentContext | Mapping[str, object] | None) -> StudentContext:
    """Parse untrusted context into the canonical model without exposing submitted values."""

    if value is None:
        return StudentContext()
    if isinstance(value, StudentContext):
        return value
    if not isinstance(value, Mapping):
        raise ContextValidationError()
    try:
        return StudentContext.model_validate(dict(value))
    except (TypeError, ValueError, ValidationError):
        raise ContextValidationError() from None
