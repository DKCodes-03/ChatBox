"""Session-cookie lifecycle and message HTTP endpoints."""

from __future__ import annotations

import asyncio
from typing import Annotated, Any, NoReturn

from fastapi import APIRouter, Cookie, Depends, Response, status

from app.api.dependencies import (
    MessageProcessor,
    PublishingMessageProcessor,
    get_message_processor,
    get_session_manager,
)
from app.api.errors import SafeAPIError, SafeErrorCode, safe_error_response, utc_now
from app.api.schemas import (
    AnswerEnvelope,
    ErrorEnvelope,
    MessageRequest,
    SessionCreateRequest,
    SessionResponse,
)
from app.sessions import (
    GenerationCancelledError,
    GenerationConflictError,
    InvalidSessionInputError,
    SessionBusyError,
    SessionCapacityError,
    SessionCleanupError,
    SessionExpiredError,
    SessionManager,
    SessionRateLimitError,
    UnknownSessionError,
)
from app.sessions.models import GenerationLease, SessionSnapshot

SESSION_COOKIE_NAME = "__Host-pnw_chat_session"
SESSION_COOKIE_PATH = "/"
CAPACITY_RETRY_AFTER_SECONDS = 60

router = APIRouter(tags=["chat"])

SessionManagerDependency = Annotated[SessionManager, Depends(get_session_manager)]
MessageProcessorDependency = Annotated[MessageProcessor, Depends(get_message_processor)]
SessionCookie = Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)]

COMMON_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorEnvelope},
    409: {"model": ErrorEnvelope},
    410: {"model": ErrorEnvelope},
    422: {"model": ErrorEnvelope},
    429: {"model": ErrorEnvelope},
    503: {"model": ErrorEnvelope},
}


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        path=SESSION_COOKIE_PATH,
        secure=True,
        httponly=True,
        samesite="strict",
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path=SESSION_COOKIE_PATH,
        secure=True,
        httponly=True,
        samesite="strict",
    )


def _require_token(token: str | None) -> str:
    if token is None or not token:
        raise SafeAPIError(SafeErrorCode.UNAUTHORIZED)
    return token


def _require_healthy_manager(manager: SessionManager) -> None:
    if manager.cleanup_failed:
        raise SafeAPIError(SafeErrorCode.PROCESSING_UNAVAILABLE)


def _session_response(snapshot: SessionSnapshot) -> SessionResponse:
    return SessionResponse(expires_at=snapshot.expires_at, server_time=utc_now())


def _raise_access_error(error: Exception) -> NoReturn:
    if isinstance(error, UnknownSessionError):
        raise SafeAPIError(SafeErrorCode.UNAUTHORIZED) from None
    if isinstance(error, SessionExpiredError):
        raise SafeAPIError(SafeErrorCode.SESSION_EXPIRED) from None
    if isinstance(error, (SessionBusyError, GenerationConflictError)):
        raise SafeAPIError(SafeErrorCode.CONFLICT) from None
    if isinstance(error, SessionRateLimitError):
        raise SafeAPIError(
            SafeErrorCode.RATE_LIMITED,
            retry_after_seconds=error.retry_after_seconds,
        ) from None
    if isinstance(error, SessionCapacityError):
        raise SafeAPIError(
            SafeErrorCode.RATE_LIMITED,
            retry_after_seconds=CAPACITY_RETRY_AFTER_SECONDS,
        ) from None
    if isinstance(error, InvalidSessionInputError):
        raise SafeAPIError(SafeErrorCode.INVALID_REQUEST) from None
    if isinstance(error, SessionCleanupError):
        raise SafeAPIError(SafeErrorCode.PROCESSING_UNAVAILABLE) from None
    raise error


def _admit_message(
    manager: SessionManager,
    *,
    token: str,
    payload: MessageRequest,
) -> GenerationLease:
    context = payload.context.model_dump(exclude_none=True) if payload.context is not None else None
    try:
        return manager.admit_student_message(token, payload.message, context=context)
    except (
        UnknownSessionError,
        SessionExpiredError,
        SessionBusyError,
        SessionRateLimitError,
        InvalidSessionInputError,
        SessionCleanupError,
    ) as error:
        _raise_access_error(error)


def _assistant_message(answer: AnswerEnvelope) -> str:
    parts = [segment.text for segment in answer.segments]
    if answer.clarification is not None:
        parts.append(answer.clarification.question)
    return "\n".join(parts)


def _abandon_message(manager: SessionManager, lease: GenerationLease) -> None:
    try:
        manager.abandon_generation(lease)
    except SessionCleanupError:
        raise SafeAPIError(SafeErrorCode.PROCESSING_UNAVAILABLE) from None


@router.post(
    "/sessions",
    response_model=SessionResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        422: {"model": ErrorEnvelope},
        429: {"model": ErrorEnvelope},
        503: {"model": ErrorEnvelope},
    },
)
def create_session(
    _payload: SessionCreateRequest,
    response: Response,
    manager: SessionManagerDependency,
) -> SessionResponse:
    """Create one memory-only session and place its credential only in a cookie."""

    _require_healthy_manager(manager)
    try:
        created = manager.create_session()
    except (SessionCapacityError, SessionCleanupError) as error:
        _raise_access_error(error)
    _set_session_cookie(response, created.token.get_secret_value())
    return _session_response(created.session)


@router.get(
    "/session",
    response_model=SessionResponse,
    responses={
        401: {"model": ErrorEnvelope},
        410: {"model": ErrorEnvelope},
        503: {"model": ErrorEnvelope},
    },
)
def get_session(
    manager: SessionManagerDependency,
    session_token: SessionCookie = None,
) -> SessionResponse:
    """Return expiry metadata without a transcript or idle-time reset."""

    _require_healthy_manager(manager)
    token = _require_token(session_token)
    try:
        snapshot = manager.inspect_session(token)
    except (UnknownSessionError, SessionExpiredError, SessionCleanupError) as error:
        _raise_access_error(error)
    return _session_response(snapshot)


@router.delete(
    "/session",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    responses={503: {"model": ErrorEnvelope}},
)
def delete_session(
    manager: SessionManagerDependency,
    session_token: SessionCookie = None,
) -> Response:
    """Invalidate a session, cancel pending work, and always clear its cookie."""

    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    if session_token is not None:
        try:
            manager.end_chat(session_token)
        except SessionCleanupError:
            error_response = safe_error_response(SafeErrorCode.PROCESSING_UNAVAILABLE)
            _clear_session_cookie(error_response)
            return error_response
    _clear_session_cookie(response)
    return response


@router.post(
    "/messages",
    response_model=AnswerEnvelope,
    responses=COMMON_ERRORS,
)
async def post_message(
    payload: MessageRequest,
    manager: SessionManagerDependency,
    processor: MessageProcessorDependency,
    session_token: SessionCookie = None,
) -> AnswerEnvelope:
    """Admit one student message and expose only a complete validated answer."""

    _require_healthy_manager(manager)
    token = _require_token(session_token)
    lease = _admit_message(manager, token=token, payload=payload)

    try:

        def publish(authorized_answer: AnswerEnvelope) -> SessionSnapshot:
            return manager.complete_generation(
                lease,
                assistant_message=_assistant_message(authorized_answer),
            )

        if isinstance(processor, PublishingMessageProcessor):
            answer, snapshot = await processor.process_and_publish(
                lease,
                publisher=publish,
            )
        else:
            answer = await processor.process(lease)
            snapshot = publish(answer)
        if not isinstance(answer, AnswerEnvelope):
            raise TypeError("message processor returned an invalid response")
    except asyncio.CancelledError:
        session_cancelled = lease.cancellation.cancelled
        _abandon_message(manager, lease)
        if session_cancelled:
            raise SafeAPIError(SafeErrorCode.SESSION_EXPIRED) from None
        raise
    except SafeAPIError:
        _abandon_message(manager, lease)
        raise
    except (SessionExpiredError, GenerationCancelledError, UnknownSessionError):
        _abandon_message(manager, lease)
        raise SafeAPIError(SafeErrorCode.SESSION_EXPIRED) from None
    except GenerationConflictError:
        _abandon_message(manager, lease)
        raise SafeAPIError(SafeErrorCode.CONFLICT) from None
    except (InvalidSessionInputError, SessionCleanupError):
        _abandon_message(manager, lease)
        raise SafeAPIError(SafeErrorCode.PROCESSING_UNAVAILABLE) from None
    except Exception:
        _abandon_message(manager, lease)
        raise SafeAPIError(SafeErrorCode.PROCESSING_UNAVAILABLE) from None

    return answer.model_copy(
        update={
            "expires_at": snapshot.expires_at,
            "server_time": utc_now(),
        }
    )
