"""FastAPI dependencies backed by lifespan-owned application state."""

from __future__ import annotations

from typing import cast

from fastapi import Request

from app.api.runtime import ReadinessProbe
from app.config import Settings


def get_app_settings(request: Request) -> Settings:
    """Return the settings validated while constructing the application."""

    return cast(Settings, request.app.state.settings)


def get_readiness_probe(request: Request) -> ReadinessProbe:
    """Return the injected readiness probe owned by the application lifespan."""

    return cast(ReadinessProbe, request.app.state.readiness_probe)
