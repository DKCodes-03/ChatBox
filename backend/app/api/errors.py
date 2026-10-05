"""Fixed, non-echoing API error envelopes and exception handlers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class SafeErrorCode(StrEnum):
    BAD_REQUEST = "bad_request"
    ORIGIN_NOT_ALLOWED = "origin_not_allowed"
    REQUEST_TOO_LARGE = "request_too_large"
    INVALID_REQUEST = "invalid_request"
    UNAUTHORIZED = "unauthorized"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    METHOD_NOT_ALLOWED = "method_not_allowed"
    CONFLICT = "conflict"
    SESSION_EXPIRED = "session_expired"
    RATE_LIMITED = "rate_limited"
    PROCESSING_UNAVAILABLE = "processing_unavailable"
    INTERNAL_ERROR = "internal_error"


@dataclass(frozen=True, slots=True)
class SafeErrorDefinition:
    status_code: int
    message: str
    retryable: bool


ERROR_DEFINITIONS: dict[SafeErrorCode, SafeErrorDefinition] = {
    SafeErrorCode.BAD_REQUEST: SafeErrorDefinition(
        400, "The request could not be processed.", False
    ),
    SafeErrorCode.ORIGIN_NOT_ALLOWED: SafeErrorDefinition(
        403,
        "The request origin is not allowed.",
        False,
    ),
    SafeErrorCode.REQUEST_TOO_LARGE: SafeErrorDefinition(
        413,
        "The request body is too large.",
        False,
    ),
    SafeErrorCode.INVALID_REQUEST: SafeErrorDefinition(
        422,
        "The request is invalid.",
        False,
    ),
    SafeErrorCode.UNAUTHORIZED: SafeErrorDefinition(401, "A valid session is required.", False),
    SafeErrorCode.FORBIDDEN: SafeErrorDefinition(403, "The request is not allowed.", False),
    SafeErrorCode.NOT_FOUND: SafeErrorDefinition(
        404, "The requested resource was not found.", False
    ),
    SafeErrorCode.METHOD_NOT_ALLOWED: SafeErrorDefinition(
        405,
        "The request method is not allowed.",
        False,
    ),
    SafeErrorCode.CONFLICT: SafeErrorDefinition(
        409, "The request conflicts with current state.", False
    ),
    SafeErrorCode.SESSION_EXPIRED: SafeErrorDefinition(410, "The session has ended.", False),
    SafeErrorCode.RATE_LIMITED: SafeErrorDefinition(
        429,
        "Too many requests were received. Try again later.",
        True,
    ),
    SafeErrorCode.PROCESSING_UNAVAILABLE: SafeErrorDefinition(
        503,
        "The service is temporarily unavailable.",
        True,
    ),
    SafeErrorCode.INTERNAL_ERROR: SafeErrorDefinition(
        500,
        "The service could not process the request.",
        True,
    ),
}

HTTP_STATUS_ERRORS: dict[int, SafeErrorCode] = {
    400: SafeErrorCode.BAD_REQUEST,
    401: SafeErrorCode.UNAUTHORIZED,
    403: SafeErrorCode.FORBIDDEN,
    404: SafeErrorCode.NOT_FOUND,
    405: SafeErrorCode.METHOD_NOT_ALLOWED,
    409: SafeErrorCode.CONFLICT,
    410: SafeErrorCode.SESSION_EXPIRED,
    413: SafeErrorCode.REQUEST_TOO_LARGE,
    422: SafeErrorCode.INVALID_REQUEST,
    429: SafeErrorCode.RATE_LIMITED,
    503: SafeErrorCode.PROCESSING_UNAVAILABLE,
}

NO_STORE_HEADERS = {
    "Cache-Control": "no-store, private",
    "Pragma": "no-cache",
    "Expires": "0",
    "X-Content-Type-Options": "nosniff",
}


class SafeAPIError(Exception):
    """Select a preapproved response without accepting arbitrary response text."""

    def __init__(self, code: SafeErrorCode, *, retry_after_seconds: int | None = None) -> None:
        super().__init__(code.value)
        self.code = code
        self.retry_after_seconds = retry_after_seconds


def utc_now() -> datetime:
    """Return a timezone-aware timestamp for response envelopes."""

    return datetime.now(UTC)


def safe_error_response(
    code: SafeErrorCode,
    *,
    retry_after_seconds: int | None = None,
) -> JSONResponse:
    """Build a fixed error envelope without request or exception content."""

    definition = ERROR_DEFINITIONS[code]
    headers = dict(NO_STORE_HEADERS)
    if retry_after_seconds is not None:
        headers["Retry-After"] = str(max(0, retry_after_seconds))
    return JSONResponse(
        status_code=definition.status_code,
        content={
            "error": {
                "code": code.value,
                "message": definition.message,
                "retryable": definition.retryable,
            },
            "server_time": utc_now().isoformat().replace("+00:00", "Z"),
        },
        headers=headers,
    )


def install_exception_handlers(app: FastAPI) -> None:
    """Register handlers that replace framework and validation details with fixed text."""

    @app.exception_handler(SafeAPIError)
    async def _safe_api_error(_request: Request, exc: SafeAPIError) -> JSONResponse:
        return safe_error_response(exc.code, retry_after_seconds=exc.retry_after_seconds)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(
        _request: Request,
        _exc: RequestValidationError,
    ) -> JSONResponse:
        return safe_error_response(SafeErrorCode.INVALID_REQUEST)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = HTTP_STATUS_ERRORS.get(exc.status_code, SafeErrorCode.INTERNAL_ERROR)
        return safe_error_response(code)

    @app.exception_handler(Exception)
    async def _unexpected_error(_request: Request, _exc: Exception) -> JSONResponse:
        return safe_error_response(SafeErrorCode.INTERNAL_ERROR)
