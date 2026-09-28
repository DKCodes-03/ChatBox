"""Deterministic checks for the exact in-memory session expiry boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from app.sessions import SessionManager
from app.sessions.exceptions import GenerationCancelledError, SessionExpiredError


@dataclass
class _Clock:
    value: datetime

    def __call__(self) -> datetime:
        return self.value

    def advance(self, delta: timedelta) -> None:
        self.value += delta


def test_session_is_valid_before_and_expired_at_exactly_thirty_minutes() -> None:
    clock = _Clock(datetime(2027, 1, 1, 12, 0, tzinfo=UTC))
    manager = SessionManager(clock=clock, auto_start=False)
    created = manager.create_session()
    token = created.token.get_secret_value()

    clock.advance(timedelta(minutes=30) - timedelta(microseconds=1))
    assert manager.inspect_session(token).expires_at == created.session.expires_at

    clock.advance(timedelta(microseconds=1))
    with pytest.raises(SessionExpiredError):
        manager.inspect_session(token)
    assert manager.active_count == 0


def test_expiry_cancels_pending_work_and_clears_its_content_buffer() -> None:
    clock = _Clock(datetime(2027, 1, 1, 12, 0, tzinfo=UTC))
    manager = SessionManager(clock=clock, auto_start=False)
    created = manager.create_session()
    token = created.token.get_secret_value()
    lease = manager.admit_student_message(
        token,
        "synthetic private marker",
        context={"campus": "Hammond"},
    )
    messages, context = lease.buffer.snapshot()
    assert messages[0].content == "synthetic private marker"
    assert context == {"campus": "Hammond"}

    clock.advance(timedelta(minutes=30))
    assert manager.expire_due_sessions() == 1

    assert lease.cancellation.cancelled
    assert lease.buffer.is_cleared
    with pytest.raises(GenerationCancelledError):
        lease.buffer.snapshot()
    with pytest.raises(SessionExpiredError):
        manager.inspect_session(token)


def test_assistant_completion_does_not_extend_student_message_expiry() -> None:
    clock = _Clock(datetime(2027, 1, 1, 12, 0, tzinfo=UTC))
    manager = SessionManager(clock=clock, auto_start=False)
    created = manager.create_session()
    token = created.token.get_secret_value()

    clock.advance(timedelta(minutes=5))
    lease = manager.admit_student_message(token, "synthetic question")
    student_expiry = manager.inspect_session(token).expires_at

    clock.advance(timedelta(minutes=20))
    completed = manager.complete_generation(lease, assistant_message="synthetic answer")
    assert completed.expires_at == student_expiry

    clock.value = student_expiry
    with pytest.raises(SessionExpiredError):
        manager.inspect_session(token)


def test_end_chat_removes_session_and_cancels_pending_generation() -> None:
    clock = _Clock(datetime(2027, 1, 1, 12, 0, tzinfo=UTC))
    manager = SessionManager(clock=clock, auto_start=False)
    created = manager.create_session()
    token = created.token.get_secret_value()
    lease = manager.admit_student_message(token, "synthetic question")

    assert manager.end_chat(token)
    assert manager.active_count == 0
    assert lease.cancellation.cancelled
    assert lease.buffer.is_cleared
