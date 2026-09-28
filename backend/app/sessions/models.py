"""Volatile session values and cancellation-aware request buffers."""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from pydantic import SecretStr

from app.sessions.exceptions import GenerationCancelledError, SessionCleanupError

CancelCallback = Callable[[], None]


class MessageRole(StrEnum):
    STUDENT = "student"
    ASSISTANT = "assistant"


@dataclass(frozen=True, slots=True)
class ConversationMessage:
    """One message retained only inside its active in-memory session."""

    role: MessageRole
    content: str


class RequestBuffer:
    """Mutable inference input that can be cleared when a request is invalidated."""

    def __init__(
        self,
        messages: list[ConversationMessage],
        context: Mapping[str, str],
    ) -> None:
        self._lock = threading.Lock()
        self._messages = messages
        self._context = dict(context)
        self._cleared = False

    def snapshot(self) -> tuple[tuple[ConversationMessage, ...], dict[str, str]]:
        with self._lock:
            if self._cleared:
                raise GenerationCancelledError()
            return tuple(self._messages), dict(self._context)

    def clear(self) -> None:
        with self._lock:
            self._messages.clear()
            self._context.clear()
            self._cleared = True

    @property
    def is_cleared(self) -> bool:
        with self._lock:
            return self._cleared


class CancellationHandle:
    """Thread-safe cancellation signal with bounded, one-shot cleanup callbacks."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._event = threading.Event()
        self._callbacks: list[CancelCallback] = []
        self._closed = False

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        return self._event.wait(timeout)

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise GenerationCancelledError()

    def add_callback(self, callback: CancelCallback) -> None:
        """Register cleanup; invoke it immediately if cancellation already occurred."""

        invoke_now = False
        with self._lock:
            if self._closed and not self._event.is_set():
                raise GenerationCancelledError()
            if self._event.is_set():
                invoke_now = True
            else:
                self._callbacks.append(callback)
        if invoke_now:
            try:
                callback()
            except Exception:
                raise SessionCleanupError() from None

    def cancel(self) -> None:
        callbacks: list[CancelCallback]
        with self._lock:
            if self._event.is_set():
                return
            self._event.set()
            self._closed = True
            callbacks = self._callbacks
            self._callbacks = []

        failed = False
        for callback in callbacks:
            try:
                callback()
            except Exception:
                failed = True
        if failed:
            raise SessionCleanupError()

    def close(self) -> None:
        """Release callback references after successful request completion."""

        with self._lock:
            self._closed = True
            self._callbacks.clear()


@dataclass(frozen=True, slots=True)
class SessionSnapshot:
    created_at: datetime
    last_student_message_at: datetime | None
    expires_at: datetime
    generation: int
    has_pending_request: bool


@dataclass(frozen=True, slots=True)
class CreatedSession:
    """A new credential and its non-content metadata; SecretStr keeps repr redacted."""

    token: SecretStr
    session: SessionSnapshot


@dataclass(frozen=True, slots=True)
class GenerationLease:
    """Capability required to commit one admitted generation result."""

    token: SecretStr = field(repr=False)
    generation: int
    cancellation: CancellationHandle = field(repr=False)
    buffer: RequestBuffer = field(repr=False)
