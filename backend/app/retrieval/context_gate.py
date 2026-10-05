"""Metadata-driven context clarification before exact evidence retrieval."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.context import (
    ContextField,
    ContextOptions,
    ContextValidationError,
    StudentContext,
    validate_context,
)
from app.generation.schemas import (
    AnswerOutcome,
    ClarificationRequest,
    ReasonCode,
    StructuredAnswer,
)

CONTEXT_FIELD_PRIORITY = (
    ContextField.CAMPUS,
    ContextField.TERM,
    ContextField.YEAR,
    ContextField.SESSION,
    ContextField.PROGRAM,
    ContextField.STUDENT_LEVEL,
    ContextField.CATALOG_YEAR,
)

_QUESTIONS = {
    ContextField.CAMPUS: "Which PNW campus should I use for this question?",
    ContextField.TERM: "Which academic term should I use for this question?",
    ContextField.YEAR: "Which academic year should I use for this question?",
    ContextField.SESSION: "Which academic session should I use for this question?",
    ContextField.PROGRAM: "Which academic program should I use for this question?",
    ContextField.STUDENT_LEVEL: "Which student level should I use for this question?",
    ContextField.CATALOG_YEAR: "Which catalog year should I use for this question?",
}


@dataclass(frozen=True, slots=True)
class ContextGateDecision:
    """The applicable explicit selection and an optional targeted clarification."""

    selected_context: StudentContext
    options: ContextOptions
    clarification: StructuredAnswer | None = None

    @property
    def requires_clarification(self) -> bool:
        return self.clarification is not None


class ContextGate:
    """Select only context supported by the current eligible evidence profiles.

    The gate is intentionally stateless. Every request is evaluated from its explicit context and
    the current eligible metadata, so a correction cannot inherit a previous retrieval selection.
    """

    def evaluate(
        self,
        *,
        context: StudentContext | Mapping[str, object] | None,
        profiles: Sequence[StudentContext | Mapping[str, object]],
    ) -> ContextGateDecision:
        explicit = validate_context(context)
        eligible_profiles = self._profiles(profiles)
        if not eligible_profiles:
            return ContextGateDecision(
                selected_context=StudentContext(),
                options=ContextOptions(),
            )

        all_options = self._options(eligible_profiles)
        explicit_values = explicit.as_field_mapping()
        selected: dict[str, str] = {}
        remaining = eligible_profiles

        for field in CONTEXT_FIELD_PRIORITY:
            eligible = all_options.for_field(field)
            supplied = explicit_values.get(field)
            if not eligible or supplied is None:
                continue
            matching = tuple(
                profile
                for profile in remaining
                if profile.as_field_mapping().get(field) == supplied
            )
            if not matching:
                return self._clarify(
                    selected=selected,
                    options=self._options(remaining),
                    field=field,
                    all_options=all_options,
                )
            selected[field.value] = supplied
            remaining = matching

        narrowed_options = self._options(remaining)
        for field in CONTEXT_FIELD_PRIORITY:
            if narrowed_options.for_field(field) and field.value not in selected:
                return self._clarify(
                    selected=selected,
                    options=narrowed_options,
                    field=field,
                    all_options=all_options,
                )

        return ContextGateDecision(
            selected_context=StudentContext.model_validate(selected),
            options=all_options,
        )

    @staticmethod
    def _profiles(
        profiles: Sequence[StudentContext | Mapping[str, object]],
    ) -> tuple[StudentContext, ...]:
        if not isinstance(profiles, Sequence) or isinstance(profiles, (str, bytes)):
            raise ContextValidationError()
        return tuple(validate_context(profile) for profile in profiles)

    @staticmethod
    def _options(profiles: Sequence[StudentContext]) -> ContextOptions:
        values: dict[str, tuple[str, ...]] = {}
        for field in CONTEXT_FIELD_PRIORITY:
            options = {
                value
                for profile in profiles
                if (value := profile.as_field_mapping().get(field)) is not None
            }
            values[field.value] = tuple(sorted(options, key=lambda item: (item.casefold(), item)))
        return ContextOptions.model_validate(values)

    @staticmethod
    def _clarify(
        *,
        selected: Mapping[str, str],
        options: ContextOptions,
        field: ContextField,
        all_options: ContextOptions,
    ) -> ContextGateDecision:
        field_options = options.for_field(field) or all_options.for_field(field)
        clarification = StructuredAnswer(
            outcome=AnswerOutcome.CLARIFICATION,
            clarification=ClarificationRequest(
                question=_QUESTIONS[field],
                fields=(field,),
                options=field_options,
            ),
            reason_code=ReasonCode.CONTEXT_REQUIRED,
        )
        return ContextGateDecision(
            selected_context=StudentContext.model_validate(dict(selected)),
            options=all_options,
            clarification=clarification,
        )


__all__ = [
    "CONTEXT_FIELD_PRIORITY",
    "ContextGate",
    "ContextGateDecision",
]
