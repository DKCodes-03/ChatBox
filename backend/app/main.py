"""FastAPI application factory and lifecycle configuration."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import api_router
from app.api.dependencies import MessageProcessor, UnavailableMessageProcessor
from app.api.errors import install_exception_handlers
from app.api.middleware import (
    NoStoreHeadersMiddleware,
    OriginValidationMiddleware,
    RequestBodyLimitMiddleware,
)
from app.api.processor import RequestMessageProcessor, build_runtime_message_processor
from app.api.runtime import ReadinessProbe, RuntimeReadinessProbe
from app.config import SecretFileError, Settings, get_settings
from app.sessions import SessionManager


def create_app(
    *,
    settings: Settings | None = None,
    readiness_probe: ReadinessProbe | None = None,
    session_manager: SessionManager | None = None,
    message_processor: MessageProcessor | None = None,
) -> FastAPI:
    """Build an application whose dependencies can be replaced in isolated tests."""

    resolved_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        owned_probe: RuntimeReadinessProbe | None = None
        owned_session_manager: SessionManager | None = None
        owned_message_processor: RequestMessageProcessor | None = None
        active_probe = readiness_probe
        if active_probe is None:
            owned_probe = RuntimeReadinessProbe(resolved_settings)
            owned_probe.startup()
            active_probe = owned_probe

        active_session_manager = session_manager
        if active_session_manager is None:
            owned_session_manager = SessionManager.from_settings(
                resolved_settings,
                auto_start=False,
            )
            owned_session_manager.start()
            active_session_manager = owned_session_manager

        active_message_processor = message_processor
        if active_message_processor is None:
            try:
                owned_message_processor = build_runtime_message_processor(resolved_settings)
                active_message_processor = owned_message_processor
            except (OSError, SecretFileError, ValueError):
                active_message_processor = UnavailableMessageProcessor()

        application.state.settings = resolved_settings
        application.state.readiness_probe = active_probe
        application.state.session_manager = active_session_manager
        application.state.message_processor = active_message_processor
        try:
            yield
        finally:
            if owned_message_processor is not None:
                owned_message_processor.close()
            if owned_session_manager is not None:
                owned_session_manager.shutdown()
            if owned_probe is not None:
                owned_probe.shutdown()

    application = FastAPI(
        title="PNW Student Information Chatbot API",
        version="0.1.0",
        lifespan=lifespan,
    )
    install_exception_handlers(application)
    application.include_router(api_router)
    application.add_middleware(
        RequestBodyLimitMiddleware,
        max_body_bytes=resolved_settings.max_request_body_bytes,
    )
    application.add_middleware(
        OriginValidationMiddleware,
        allowed_origin=str(resolved_settings.public_origin),
    )
    application.add_middleware(NoStoreHeadersMiddleware)
    return application


app = create_app()
