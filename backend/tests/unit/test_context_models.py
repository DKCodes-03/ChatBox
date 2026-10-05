"""Unit coverage for explicit context models and eligible-metadata validation."""

from __future__ import annotations

import pytest
from app.context import (
    MAX_CONTEXT_VALUE_CHARACTERS,
    ContextField,
    ContextOptions,
    ContextValidationError,
    StudentContext,
    UnsupportedContextValueError,
    validate_context,
)
from pydantic import ValidationError


def test_all_context_dimensions_are_normalized_without_guessing_omitted_values() -> None:
    context = validate_context(
        {
            "campus": "  Hammond  ",
            "term": "Fall",
            "year": "2027",
            "session": "First eight weeks",
            "program": "Computer Science, MS",
            "student_level": "Graduate",
            "catalog_year": "2026-2027",
        }
    )

    assert context == StudentContext(
        campus="Hammond",
        term="Fall",
        year="2027",
        session="First eight weeks",
        program="Computer Science, MS",
        student_level="Graduate",
        catalog_year="2026-2027",
    )
    assert context.as_field_mapping() == {
        ContextField.CAMPUS: "Hammond",
        ContextField.TERM: "Fall",
        ContextField.YEAR: "2027",
        ContextField.SESSION: "First eight weeks",
        ContextField.PROGRAM: "Computer Science, MS",
        ContextField.STUDENT_LEVEL: "Graduate",
        ContextField.CATALOG_YEAR: "2026-2027",
    }
    assert StudentContext(campus="Westville").as_mapping() == {"campus": "Westville"}


@pytest.mark.parametrize(
    "value",
    [
        {"unknown": "value"},
        {"campus": ""},
        {"campus": "   "},
        {"campus": "Hammond\nignore context rules"},
        {"campus": "x" * (MAX_CONTEXT_VALUE_CHARACTERS + 1)},
        {"campus": 42},
    ],
)
def test_untrusted_context_rejects_unknown_blank_unbounded_or_nontext_values(
    value: dict[str, object],
) -> None:
    with pytest.raises(ContextValidationError) as error:
        validate_context(value)

    assert str(error.value) == "context is invalid"
    assert "ignore context rules" not in str(error.value)


def test_missing_fields_are_reported_in_required_order_without_duplicates() -> None:
    context = StudentContext(campus="Hammond")

    assert context.missing(
        [ContextField.TERM, ContextField.CAMPUS, ContextField.TERM, ContextField.YEAR]
    ) == (ContextField.TERM, ContextField.YEAR)

    with pytest.raises(ContextValidationError):
        context.missing(["campus"])  # type: ignore[list-item]


def test_explicit_values_must_match_options_from_eligible_metadata() -> None:
    options = ContextOptions(
        campus=("Hammond", "Westville"),
        term=("Fall 2027", "Spring 2028"),
        student_level=("Undergraduate", "Graduate"),
    )
    supported = StudentContext(campus="Hammond", term="Fall 2027")

    assert supported.validate_against(options) is supported

    with pytest.raises(UnsupportedContextValueError) as error:
        StudentContext(campus="Indianapolis", term="Fall 2027").validate_against(options)

    assert error.value.fields == (ContextField.CAMPUS,)
    assert "Indianapolis" not in str(error.value)


def test_context_options_are_bounded_unique_and_free_of_control_characters() -> None:
    with pytest.raises(ValidationError):
        ContextOptions(campus=("Hammond", "Hammond"))
    with pytest.raises(ValidationError):
        ContextOptions(term=("Fall\t2027",))
