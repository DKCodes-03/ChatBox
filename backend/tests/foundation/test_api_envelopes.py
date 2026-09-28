"""Foundational HTTP envelope, privacy, and cache-control checks."""

from __future__ import annotations

from datetime import datetime

from app.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient
from httpx import Headers
from pydantic import AnyHttpUrl


class _ReadinessProbe:
    def __init__(self, ready: bool) -> None:
        self.ready = ready

    def is_ready(self) -> bool:
        return self.ready


def _client(*, ready: bool) -> TestClient:
    settings = Settings(public_origin=AnyHttpUrl("http://testserver"))
    return TestClient(create_app(settings=settings, readiness_probe=_ReadinessProbe(ready)))


def _assert_no_store(headers: Headers) -> None:
    assert headers["cache-control"] == "no-store, private"
    assert headers["pragma"] == "no-cache"
    assert headers["expires"] == "0"
    assert headers["x-content-type-options"] == "nosniff"


def test_live_health_uses_the_success_envelope_without_cache_storage() -> None:
    with _client(ready=True) as client:
        response = client.get("/api/v1/health/live")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"status", "server_time"}
    assert payload["status"] == "ok"
    assert datetime.fromisoformat(payload["server_time"]).tzinfo is not None
    _assert_no_store(response.headers)


def test_unready_health_returns_a_fixed_non_sensitive_error_envelope() -> None:
    with _client(ready=False) as client:
        response = client.get("/api/v1/health/ready")

    assert response.status_code == 503
    payload = response.json()
    assert set(payload) == {"error", "server_time"}
    assert payload["error"] == {
        "code": "processing_unavailable",
        "message": "The service is temporarily unavailable.",
        "retryable": True,
    }
    assert "database" not in response.text.casefold()
    assert "model" not in response.text.casefold()
    _assert_no_store(response.headers)


def test_unknown_route_does_not_echo_a_path_or_query_marker() -> None:
    marker = "distinctive-student-marker"
    with _client(ready=True) as client:
        response = client.get(f"/api/v1/{marker}?question={marker}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    assert marker not in response.text
    _assert_no_store(response.headers)


def test_oversized_body_is_rejected_before_routing_without_echoing_content() -> None:
    marker = "private-marker"
    body = (marker * 900).encode()
    with _client(ready=True) as client:
        response = client.post(
            "/api/v1/health/live",
            content=body,
            headers={"Origin": "http://testserver"},
        )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_too_large"
    assert marker not in response.text
    _assert_no_store(response.headers)


def test_state_changing_request_requires_the_configured_origin() -> None:
    with _client(ready=True) as client:
        response = client.post(
            "/api/v1/health/live",
            content=b"",
            headers={"Origin": "https://attacker.example"},
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "origin_not_allowed"
    _assert_no_store(response.headers)
