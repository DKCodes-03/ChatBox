"""FastAPI dependencies backed by lifespan-owned application state."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, cast, runtime_checkable

from fastapi import Request

from app.api.errors import SafeAPIError, SafeErrorCode
from app.api.runtime import ReadinessProbe
from app.api.schemas import AnswerEnvelope
from app.config import Settings
from app.sessions import GenerationLease, SessionManager, SessionSnapshot


class MessageProcessor(Protocol):
    """Complete one admitted request without owning session lifecycle state."""

    async def process(self, lease: GenerationLease) -> AnswerEnvelope:
        """Return a fully validated answer for the session-owned request buffer."""
        ...


@runtime_checkable
class PublishingMessageProcessor(Protocol):
    """A processor that holds final authority locks through session publication."""

    async def process_and_publish(
        self,
        lease: GenerationLease,
        *,
        publisher: Callable[[AnswerEnvelope], SessionSnapshot],
    ) -> tuple[AnswerEnvelope, SessionSnapshot]: ...


class UnavailableMessageProcessor:
    """Fail closed until the bounded prompt coordinator is configured."""

    async def process(self, _lease: GenerationLease) -> AnswerEnvelope:
        raise SafeAPIError(SafeErrorCode.PROCESSING_UNAVAILABLE)


def get_app_settings(request: Request) -> Settings:
    """Return the settings validated while constructing the application."""

    return cast(Settings, request.app.state.settings)


def get_readiness_probe(request: Request) -> ReadinessProbe:
    """Return the injected readiness probe owned by the application lifespan."""

    return cast(ReadinessProbe, request.app.state.readiness_probe)


def get_session_manager(request: Request) -> SessionManager:
    """Return the single lifespan-owned in-memory session manager."""

    return cast(SessionManager, request.app.state.session_manager)


def get_message_processor(request: Request) -> MessageProcessor:
    """Return the injected transient message processor."""

    return cast(MessageProcessor, request.app.state.message_processor)
