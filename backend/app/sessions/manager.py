"""Single-process, memory-only session lifecycle and generation coordination."""

from __future__ import annotations

import math
import secrets
import threading
from _thread import LockType
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Never

from pydantic import SecretStr

from app.config import Settings
from app.context import ContextValidationError, validate_context
from app.sessions.exceptions import (
    GenerationCancelledError,
    GenerationConflictError,
    InvalidSessionInputError,
    SessionBusyError,
    SessionCapacityError,
    SessionCleanupError,
    SessionExpiredError,
    SessionRateLimitError,
    UnknownSessionError,
)
from app.sessions.models import (
    CancellationHandle,
    ConversationMessage,
    CreatedSession,
    GenerationLease,
    MessageRole,
    RequestBuffer,
    SessionSnapshot,
)

TOKEN_BYTES = 32
DEFAULT_IDLE_TIMEOUT = timedelta(minutes=30)
DEFAULT_TOMBSTONE_TTL = timedelta(seconds=60)
DEFAULT_RATE_WINDOW = timedelta(minutes=1)
DEFAULT_SWEEP_INTERVAL_SECONDS = 1.0
MAX_ASSISTANT_MESSAGE_CHARACTERS = 16_384

Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class _Session:
    created_at: datetime
    last_student_message_at: datetime | None
    expires_at: datetime
    generation: int = 0
    messages: list[ConversationMessage] = field(default_factory=list)
    context: dict[str, str] = field(default_factory=dict)
    accepted_message_times: deque[datetime] = field(default_factory=deque)
    pending_request: GenerationLease | None = None
    lock: LockType = field(default_factory=threading.Lock, repr=False)


class SessionManager:
    """Own all volatile sessions for exactly one API process."""

    def __init__(
        self,
        *,
        idle_timeout: timedelta = DEFAULT_IDLE_TIMEOUT,
        max_active_sessions: int = 200,
        messages_per_minute: int = 6,
        max_message_characters: int = 4_000,
        clock: Clock = _utc_now,
        tombstone_ttl: timedelta = DEFAULT_TOMBSTONE_TTL,
        sweep_interval_seconds: float = DEFAULT_SWEEP_INTERVAL_SECONDS,
        auto_start: bool = True,
    ) -> None:
        if idle_timeout != DEFAULT_IDLE_TIMEOUT:
            raise ValueError("session idle timeout must remain 30 minutes")
        if max_active_sessions < 1 or max_active_sessions > 200:
            raise ValueError("active session capacity must be between 1 and 200")
        if messages_per_minute < 1 or messages_per_minute > 6:
            raise ValueError("session message rate must be between 1 and 6 per minute")
        if max_message_characters < 1 or max_message_characters > 4_000:
            raise ValueError("student messages must be limited to at most 4000 characters")
        if tombstone_ttl <= timedelta(0):
            raise ValueError("tombstone lifetime must be positive")
        if sweep_interval_seconds <= 0:
            raise ValueError("expiry sweep interval must be positive")

        self._idle_timeout = idle_timeout
        self._max_active_sessions = max_active_sessions
        self._messages_per_minute = messages_per_minute
        self._max_message_characters = max_message_characters
        self._clock = clock
        self._tombstone_ttl = tombstone_ttl
        self._sweep_interval_seconds = sweep_interval_seconds
        self._sessions: dict[str, _Session] = {}
        self._expired_tombstones: dict[str, datetime] = {}
        self._registry_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._cleanup_failed = threading.Event()
        self._worker: threading.Thread | None = None
        if auto_start:
            self.start()

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        clock: Clock = _utc_now,
        auto_start: bool = True,
    ) -> SessionManager:
        return cls(
            idle_timeout=timedelta(seconds=settings.session_idle_seconds),
            max_active_sessions=settings.max_active_sessions,
            messages_per_minute=settings.session_messages_per_minute,
            max_message_characters=settings.max_question_characters,
            clock=clock,
            auto_start=auto_start,
        )

    def __enter__(self) -> SessionManager:
        self.start()
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self.shutdown()

    @property
    def cleanup_failed(self) -> bool:
        return self._cleanup_failed.is_set()

    @property
    def active_count(self) -> int:
        with self._registry_lock:
            return len(self._sessions)

    def start(self) -> None:
        with self._registry_lock:
            if self._worker is not None and self._worker.is_alive():
                return
            self._stop_event.clear()
            self._worker = threading.Thread(
                target=self._expiry_worker,
                name="session-expiry",
                daemon=True,
            )
            self._worker.start()

    def shutdown(self) -> None:
        self._stop_event.set()
        worker = self._worker
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=max(1.0, self._sweep_interval_seconds * 2))

        pending_requests: list[GenerationLease] = []
        with self._registry_lock:
            for token, session in list(self._sessions.items()):
                with session.lock:
                    pending = self._invalidate_locked(token, session, expired=False)
                    if pending is not None:
                        pending_requests.append(pending)
            self._expired_tombstones.clear()
            self._worker = None
        self._cancel_pending(pending_requests)

    def create_session(self) -> CreatedSession:
        now = self._current_time()
        self.expire_due_sessions(now=now)
        with self._registry_lock:
            self._purge_tombstones_locked(now)
            if len(self._sessions) >= self._max_active_sessions:
                raise SessionCapacityError()
            token = self._new_unique_token_locked()
            session = _Session(
                created_at=now,
                last_student_message_at=None,
                expires_at=now + self._idle_timeout,
            )
            self._sessions[token] = session
            snapshot = self._snapshot_locked(session)
        return CreatedSession(token=SecretStr(token), session=snapshot)

    def inspect_session(self, token: str | SecretStr) -> SessionSnapshot:
        raw_token = self._raw_token(token)
        now = self._current_time()
        pending: GenerationLease | None = None
        with self._registry_lock:
            session = self._sessions.get(raw_token)
            if session is None:
                self._raise_missing_locked(raw_token, now)
            with session.lock:
                if now >= session.expires_at:
                    pending = self._invalidate_locked(raw_token, session, expired=True, now=now)
                else:
                    return self._snapshot_locked(session)
        self._cancel_pending([pending] if pending is not None else [])
        raise SessionExpiredError()

    def admit_student_message(
        self,
        token: str | SecretStr,
        message: str,
        *,
        context: Mapping[str, str] | None = None,
    ) -> GenerationLease:
        self._validate_student_message(message)
        validated_context = self._validate_context(context) if context is not None else None
        raw_token = self._raw_token(token)
        now = self._current_time()
        expired_pending: GenerationLease | None = None

        with self._registry_lock:
            session = self._sessions.get(raw_token)
            if session is None:
                self._raise_missing_locked(raw_token, now)
            with session.lock:
                if now >= session.expires_at:
                    expired_pending = self._invalidate_locked(
                        raw_token,
                        session,
                        expired=True,
                        now=now,
                    )
                else:
                    if session.pending_request is not None:
                        raise SessionBusyError()
                    self._enforce_rate_limit_locked(session, now)
                    if validated_context is not None:
                        session.context.clear()
                        session.context.update(validated_context)
                    session.messages.append(
                        ConversationMessage(role=MessageRole.STUDENT, content=message)
                    )
                    session.accepted_message_times.append(now)
                    session.last_student_message_at = now
                    session.expires_at = now + self._idle_timeout
                    session.generation += 1
                    lease = GenerationLease(
                        token=SecretStr(raw_token),
                        generation=session.generation,
                        cancellation=CancellationHandle(),
                        buffer=RequestBuffer(list(session.messages), session.context),
                    )
                    session.pending_request = lease
                    return lease

        self._cancel_pending([expired_pending] if expired_pending is not None else [])
        raise SessionExpiredError()

    def complete_generation(
        self,
        lease: GenerationLease,
        *,
        assistant_message: str | None,
    ) -> SessionSnapshot:
        if assistant_message is not None and (
            not assistant_message or len(assistant_message) > MAX_ASSISTANT_MESSAGE_CHARACTERS
        ):
            raise InvalidSessionInputError()
        raw_token = lease.token.get_secret_value()
        now = self._current_time()
        expired_pending: GenerationLease | None = None

        with self._registry_lock:
            session = self._sessions.get(raw_token)
            if session is None:
                self._raise_missing_locked(raw_token, now)
            with session.lock:
                if now >= session.expires_at:
                    expired_pending = self._invalidate_locked(
                        raw_token,
                        session,
                        expired=True,
                        now=now,
                    )
                else:
                    self._assert_lease_locked(session, lease)
                    if lease.cancellation.cancelled:
                        session.pending_request = None
                        lease.buffer.clear()
                        raise GenerationCancelledError()
                    if assistant_message is not None:
                        session.messages.append(
                            ConversationMessage(
                                role=MessageRole.ASSISTANT,
                                content=assistant_message,
                            )
                        )
                    session.pending_request = None
                    snapshot = self._snapshot_locked(session)

        if expired_pending is not None:
            self._cancel_pending([expired_pending])
            raise SessionExpiredError()
        lease.buffer.clear()
        lease.cancellation.close()
        return snapshot

    def abandon_generation(self, lease: GenerationLease) -> None:
        raw_token = lease.token.get_secret_value()
        with self._registry_lock:
            session = self._sessions.get(raw_token)
            if session is not None:
                with session.lock:
                    if session.pending_request is lease:
                        session.pending_request = None
        lease.buffer.clear()
        self._cancel_pending([lease])

    def end_chat(self, token: str | SecretStr) -> bool:
        raw_token = self._raw_token(token)
        pending: GenerationLease | None = None
        with self._registry_lock:
            session = self._sessions.get(raw_token)
            if session is None:
                self._expired_tombstones.pop(raw_token, None)
                return False
            with session.lock:
                pending = self._invalidate_locked(raw_token, session, expired=False)
        self._cancel_pending([pending] if pending is not None else [])
        return True

    def expire_due_sessions(self, *, now: datetime | None = None) -> int:
        observed_at = self._current_time() if now is None else self._validate_time(now)
        pending_requests: list[GenerationLease] = []
        expired_count = 0
        with self._registry_lock:
            self._purge_tombstones_locked(observed_at)
            for token, session in list(self._sessions.items()):
                with session.lock:
                    if observed_at < session.expires_at:
                        continue
                    pending = self._invalidate_locked(
                        token,
                        session,
                        expired=True,
                        now=observed_at,
                    )
                    if pending is not None:
                        pending_requests.append(pending)
                    expired_count += 1
        self._cancel_pending(pending_requests)
        return expired_count

    def _expiry_worker(self) -> None:
        while not self._stop_event.wait(self._sweep_interval_seconds):
            try:
                self.expire_due_sessions()
            except SessionCleanupError:
                self._cleanup_failed.set()

    def _current_time(self) -> datetime:
        return self._validate_time(self._clock())

    @staticmethod
    def _validate_time(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("session clock must return a timezone-aware timestamp")
        return value.astimezone(UTC)

    @staticmethod
    def _raw_token(token: str | SecretStr) -> str:
        raw_token = token.get_secret_value() if isinstance(token, SecretStr) else token
        if not raw_token:
            raise UnknownSessionError()
        return raw_token

    def _new_unique_token_locked(self) -> str:
        while True:
            token = secrets.token_urlsafe(TOKEN_BYTES)
            if token not in self._sessions and token not in self._expired_tombstones:
                return token

    def _raise_missing_locked(self, token: str, now: datetime) -> Never:
        self._purge_tombstones_locked(now)
        if token in self._expired_tombstones:
            raise SessionExpiredError()
        raise UnknownSessionError()

    def _invalidate_locked(
        self,
        token: str,
        session: _Session,
        *,
        expired: bool,
        now: datetime | None = None,
    ) -> GenerationLease | None:
        # Generation changes before any content is cleared or external cancellation is invoked.
        session.generation += 1
        pending = session.pending_request
        session.pending_request = None
        session.messages.clear()
        session.context.clear()
        session.accepted_message_times.clear()
        self._sessions.pop(token, None)
        if expired:
            observed_at = self._current_time() if now is None else now
            self._expired_tombstones[token] = observed_at + self._tombstone_ttl
        return pending

    @staticmethod
    def _snapshot_locked(session: _Session) -> SessionSnapshot:
        return SessionSnapshot(
            created_at=session.created_at,
            last_student_message_at=session.last_student_message_at,
            expires_at=session.expires_at,
            generation=session.generation,
            has_pending_request=session.pending_request is not None,
        )

    def _enforce_rate_limit_locked(self, session: _Session, now: datetime) -> None:
        cutoff = now - DEFAULT_RATE_WINDOW
        while session.accepted_message_times and session.accepted_message_times[0] <= cutoff:
            session.accepted_message_times.popleft()
        if len(session.accepted_message_times) < self._messages_per_minute:
            return
        retry_at = session.accepted_message_times[0] + DEFAULT_RATE_WINDOW
        retry_seconds = math.ceil((retry_at - now).total_seconds())
        raise SessionRateLimitError(retry_seconds)

    def _assert_lease_locked(self, session: _Session, lease: GenerationLease) -> None:
        if session.generation != lease.generation or session.pending_request is not lease:
            raise GenerationConflictError()

    def _purge_tombstones_locked(self, now: datetime) -> None:
        for token, delete_at in list(self._expired_tombstones.items()):
            if now >= delete_at:
                self._expired_tombstones.pop(token, None)

    def _validate_student_message(self, message: str) -> None:
        if (
            not isinstance(message, str)
            or not message
            or len(message) > self._max_message_characters
        ):
            raise InvalidSessionInputError()

    @staticmethod
    def _validate_context(context: Mapping[str, str]) -> dict[str, str]:
        try:
            return validate_context(context).as_mapping()
        except ContextValidationError:
            raise InvalidSessionInputError() from None

    def _cancel_pending(self, leases: list[GenerationLease]) -> None:
        failed = False
        for lease in leases:
            lease.buffer.clear()
            try:
                lease.cancellation.cancel()
            except SessionCleanupError:
                failed = True
        if failed:
            self._cleanup_failed.set()
            raise SessionCleanupError()
