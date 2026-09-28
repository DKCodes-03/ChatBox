"""Non-sensitive process liveness and dependency readiness routes."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.dependencies import get_readiness_probe
from app.api.errors import SafeAPIError, SafeErrorCode, utc_now
from app.api.runtime import ReadinessProbe

router = APIRouter(prefix="/health", tags=["health"])


class HealthResponse(BaseModel):
    """Minimal health response without infrastructure or corpus details."""

    status: Literal["ok"] = "ok"
    server_time: datetime


@router.get("/live", response_model=HealthResponse)
def live() -> HealthResponse:
    """Report that the process can serve HTTP."""

    return HealthResponse(server_time=utc_now())


@router.get("/ready", response_model=HealthResponse)
def ready(
    probe: Annotated[ReadinessProbe, Depends(get_readiness_probe)],
) -> HealthResponse:
    """Report readiness only when required dependencies and eligible evidence are usable."""

    if not probe.is_ready():
        raise SafeAPIError(SafeErrorCode.PROCESSING_UNAVAILABLE)
    return HealthResponse(server_time=utc_now())
