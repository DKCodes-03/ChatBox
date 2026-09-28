"""FastAPI application factory and lifecycle configuration."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import api_router
from app.api.errors import install_exception_handlers
from app.api.middleware import (
    NoStoreHeadersMiddleware,
    OriginValidationMiddleware,
    RequestBodyLimitMiddleware,
)
from app.api.runtime import ReadinessProbe, RuntimeReadinessProbe
from app.config import Settings, get_settings


def create_app(
    *,
    settings: Settings | None = None,
    readiness_probe: ReadinessProbe | None = None,
) -> FastAPI:
    """Build an application whose dependencies can be replaced in isolated tests."""

    resolved_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        owned_probe: RuntimeReadinessProbe | None = None
        active_probe = readiness_probe
        if active_probe is None:
            owned_probe = RuntimeReadinessProbe(resolved_settings)
            owned_probe.startup()
            active_probe = owned_probe

        application.state.settings = resolved_settings
        application.state.readiness_probe = active_probe
        try:
            yield
        finally:
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
