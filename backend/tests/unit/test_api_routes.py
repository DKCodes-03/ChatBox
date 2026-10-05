"""Focused endpoint checks for the session and message HTTP lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.api.schemas import (
    AnswerEnvelope,
    AnswerOutcome,
    AnswerSegment,
    Context,
    ReasonCode,
    SegmentKind,
)
from app.config import Settings
from app.main import create_app
from app.sessions import GenerationLease, SessionManager
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import AnyHttpUrl

ORIGIN = "https://testserver"
ORIGIN_HEADERS = {"Origin": ORIGIN}


class _ReadinessProbe:
    def is_ready(self) -> bool:
        return True


@dataclass
class _Clock:
    value: datetime

    def __call__(self) -> datetime:
        return self.value

    def advance(self, delta: timedelta) -> None:
        self.value += delta


class _SafeProcessor:
    def __init__(self) -> None:
        self.snapshots: list[tuple[str, dict[str, str]]] = []

    async def process(self, lease: GenerationLease) -> AnswerEnvelope:
        messages, context = lease.buffer.snapshot()
        self.snapshots.append((messages[-1].content, context))
        now = datetime.now(UTC)
        return AnswerEnvelope(
            outcome=AnswerOutcome.UNABLE,
            segments=(
                AnswerSegment(
                    kind=SegmentKind.LIMITATION,
                    text="I cannot provide a reliable answer yet.",
                    citation_ids=(),
                ),
            ),
            citations=(),
            context=Context(**context),
            clarification=None,
            referral=None,
            reason_code=ReasonCode.MISSING_EVIDENCE,
            expires_at=now,
            server_time=now,
        )


def _app(
    *,
    manager: SessionManager | None = None,
    processor: _SafeProcessor | None = None,
) -> FastAPI:
    settings = Settings(public_origin=AnyHttpUrl(ORIGIN))
    return create_app(
        settings=settings,
        readiness_probe=_ReadinessProbe(),
        session_manager=manager,
        message_processor=processor,
    )


def test_create_inspect_and_idempotently_delete_session() -> None:
    with TestClient(_app(), base_url=ORIGIN) as client:
        created = client.post("/api/v1/sessions", json={}, headers=ORIGIN_HEADERS)

        assert created.status_code == 201
        assert set(created.json()) == {"expires_at", "server_time"}
        set_cookie = created.headers["set-cookie"]
        assert "__Host-pnw_chat_session=" in set_cookie
        assert "HttpOnly" in set_cookie
        assert "Secure" in set_cookie
        assert "SameSite=strict" in set_cookie
        assert "__Host-pnw_chat_session" not in created.text

        inspected = client.get("/api/v1/session")
        assert inspected.status_code == 200
        assert inspected.json()["expires_at"] == created.json()["expires_at"]

        ended = client.delete("/api/v1/session", headers=ORIGIN_HEADERS)
        assert ended.status_code == 204
        assert ended.content == b""
        assert client.get("/api/v1/session").status_code == 401
        assert client.delete("/api/v1/session", headers=ORIGIN_HEADERS).status_code == 204


def test_message_resets_expiry_and_returns_required_null_fields() -> None:
    clock = _Clock(datetime(2027, 1, 1, 12, 0, tzinfo=UTC))
    manager = SessionManager(clock=clock, auto_start=False)
    processor = _SafeProcessor()

    with TestClient(_app(manager=manager, processor=processor), base_url=ORIGIN) as client:
        created = client.post("/api/v1/sessions", json={}, headers=ORIGIN_HEADERS)
        initial_expiry = created.json()["expires_at"]
        clock.advance(timedelta(minutes=5))

        answered = client.post(
            "/api/v1/messages",
            json={"message": "Where do I start?", "context": {"campus": "Hammond"}},
            headers=ORIGIN_HEADERS,
        )

        assert answered.status_code == 200
        payload = answered.json()
        assert payload["outcome"] == "unable"
        assert payload["context"] == {"campus": "Hammond"}
        assert payload["clarification"] is None
        assert payload["referral"] is None
        assert payload["reason_code"] == "missing_evidence"
        assert payload["expires_at"] != initial_expiry
        assert datetime.fromisoformat(payload["expires_at"]) == clock.value + timedelta(minutes=30)
        assert processor.snapshots == [("Where do I start?", {"campus": "Hammond"})]


def test_message_without_a_session_is_unauthorized_and_not_echoed() -> None:
    marker = "distinctive-private-marker"
    with TestClient(_app(processor=_SafeProcessor()), base_url=ORIGIN) as client:
        response = client.post(
            "/api/v1/messages",
            json={"message": marker},
            headers=ORIGIN_HEADERS,
        )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    assert marker not in response.text


def test_unknown_fields_use_the_sanitized_validation_error() -> None:
    marker = "distinctive-private-marker"
    with TestClient(_app(), base_url=ORIGIN) as client:
        response = client.post(
            "/api/v1/sessions",
            json={"unexpected": marker},
            headers=ORIGIN_HEADERS,
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"
    assert marker not in response.text


def test_session_capacity_returns_429_with_retry_after() -> None:
    manager = SessionManager(max_active_sessions=1, auto_start=False)
    with TestClient(_app(manager=manager), base_url=ORIGIN) as client:
        assert client.post("/api/v1/sessions", json={}, headers=ORIGIN_HEADERS).status_code == 201
        response = client.post("/api/v1/sessions", json={}, headers=ORIGIN_HEADERS)

    assert response.status_code == 429
    assert response.headers["retry-after"] == "60"
    assert response.json()["error"]["code"] == "rate_limited"


def test_unconfigured_message_processor_fails_closed_and_releases_request() -> None:
    manager = SessionManager(auto_start=False)
    with TestClient(_app(manager=manager), base_url=ORIGIN) as client:
        assert client.post("/api/v1/sessions", json={}, headers=ORIGIN_HEADERS).status_code == 201

        response = client.post(
            "/api/v1/messages",
            json={"message": "Synthetic question"},
            headers=ORIGIN_HEADERS,
        )

        assert response.status_code == 503
        assert response.json()["error"]["code"] == "processing_unavailable"
        assert client.get("/api/v1/session").status_code == 200
