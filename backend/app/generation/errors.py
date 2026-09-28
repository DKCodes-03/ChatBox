"""Fail-closed generation error classification, fallback, and safe responses."""

from __future__ import annotations

import asyncio
from enum import StrEnum
from typing import ClassVar

import httpx
from app.api.errors import SafeAPIError, SafeErrorCode
from app.config import SecretFileError
from app.generation.adapter import GenerationCancellation, GenerationRequest, LLMAdapter
from app.generation.local import (
    InvalidLocalInferenceResponseError,
    LocalInferenceCleanupError,
    LocalInferenceError,
)
from app.generation.schemas import (
    AnswerOutcome,
    AnswerSegment,
    ReasonCode,
    SegmentKind,
    StructuredAnswer,
)
from google.genai import errors as genai_errors
from pydantic import ValidationError

SAFE_PROCESSING_LIMITATION = (
    "I cannot provide a reliable answer right now because the answer service is temporarily "
    "unavailable. Please try again later."
)


class GenerationFailureKind(StrEnum):
    """Bounded failure categories that contain no provider or student content."""

    QUOTA = "quota"
    TIMEOUT = "timeout"
    INVALID_PAYLOAD = "invalid_payload"
    DATA_POLICY = "data_policy"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    FALLBACK_UNAVAILABLE = "fallback_unavailable"


class GenerationFailureError(RuntimeError):
    """Sanitized base error used after discarding provider exception details."""

    kind: ClassVar[GenerationFailureKind]
    retryable: ClassVar[bool]
    fallback_eligible: ClassVar[bool]

    def __init__(self) -> None:
        super().__init__("generation is unavailable")


class ProviderQuotaError(GenerationFailureError):
    kind = GenerationFailureKind.QUOTA
    retryable = True
    fallback_eligible = True


class ProviderTimeoutError(GenerationFailureError):
    kind = GenerationFailureKind.TIMEOUT
    retryable = True
    fallback_eligible = True


class ProviderInvalidPayloadError(GenerationFailureError):
    kind = GenerationFailureKind.INVALID_PAYLOAD
    retryable = True
    fallback_eligible = True


class ProviderDataPolicyError(GenerationFailureError):
    kind = GenerationFailureKind.DATA_POLICY
    retryable = False
    fallback_eligible = True


class ProviderUnavailableError(GenerationFailureError):
    kind = GenerationFailureKind.PROVIDER_UNAVAILABLE
    retryable = True
    fallback_eligible = True


class FallbackUnavailableError(GenerationFailureError):
    kind = GenerationFailureKind.FALLBACK_UNAVAILABLE
    retryable = True
    fallback_eligible = False

    def __init__(
        self,
        *,
        primary_kind: GenerationFailureKind,
        fallback_kind: GenerationFailureKind,
    ) -> None:
        super().__init__()
        self.primary_kind = primary_kind
        self.fallback_kind = fallback_kind


def classify_generation_error(error: Exception) -> GenerationFailureError | None:
    """Replace known provider errors with bounded errors that retain no raw details."""

    if isinstance(error, GenerationFailureError):
        return error
    if isinstance(error, genai_errors.UnknownApiResponseError):
        return ProviderInvalidPayloadError()
    if isinstance(error, (ValidationError, InvalidLocalInferenceResponseError)):
        return ProviderInvalidPayloadError()
    if isinstance(error, (TimeoutError, httpx.TimeoutException)):
        return ProviderTimeoutError()
    if isinstance(error, genai_errors.APIError):
        if error.code == 429:
            return ProviderQuotaError()
        if error.code in {408, 504}:
            return ProviderTimeoutError()
        return ProviderUnavailableError()
    if isinstance(error, LocalInferenceCleanupError):
        return ProviderUnavailableError()
    if isinstance(error, LocalInferenceError):
        return ProviderUnavailableError()
    if isinstance(error, (SecretFileError, httpx.HTTPError, ConnectionError, OSError)):
        return ProviderUnavailableError()
    return None


async def generate_with_optional_fallback(
    primary: LLMAdapter,
    request: GenerationRequest,
    *,
    cancellation: GenerationCancellation,
    fallback: LLMAdapter | None = None,
    primary_permitted: bool = True,
) -> StructuredAnswer:
    """Generate once per provider and expose only sanitized expected failures."""

    if fallback is primary:
        raise ValueError("fallback provider must differ from the primary provider")

    primary_failure: GenerationFailureError
    if primary_permitted:
        try:
            return await primary.generate(request, cancellation=cancellation)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            classified = classify_generation_error(error)
            if classified is None:
                raise
            primary_failure = classified
    else:
        primary_failure = ProviderDataPolicyError()

    cancellation.raise_if_cancelled()
    if fallback is None or not primary_failure.fallback_eligible:
        raise primary_failure from None

    try:
        return await fallback.generate(request, cancellation=cancellation)
    except asyncio.CancelledError:
        raise
    except Exception as error:
        classified = classify_generation_error(error)
        if classified is None:
            raise
        raise FallbackUnavailableError(
            primary_kind=primary_failure.kind,
            fallback_kind=classified.kind,
        ) from None


def safe_generation_limitation(failure: GenerationFailureError) -> StructuredAnswer:
    """Convert an exhausted expected failure into a fixed, unsupported-claim-free limitation."""

    if not isinstance(failure, GenerationFailureError):
        raise TypeError("a classified generation failure is required")
    return StructuredAnswer(
        outcome=AnswerOutcome.UNABLE,
        segments=(
            AnswerSegment(
                kind=SegmentKind.LIMITATION,
                text=SAFE_PROCESSING_LIMITATION,
                evidence_ids=(),
            ),
        ),
        clarification=None,
        reason_code=ReasonCode.PROCESSING_UNAVAILABLE,
    )


def sanitized_generation_503(failure: GenerationFailureError) -> SafeAPIError:
    """Convert an exhausted expected failure into the fixed processing-unavailable envelope."""

    if not isinstance(failure, GenerationFailureError):
        raise TypeError("a classified generation failure is required")
    return SafeAPIError(SafeErrorCode.PROCESSING_UNAVAILABLE)
