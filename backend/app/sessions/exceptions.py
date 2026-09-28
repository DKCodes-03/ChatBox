"""Sanitized session lifecycle errors that never include tokens or conversation content."""

from __future__ import annotations


class SessionError(RuntimeError):
    """Base class for bounded session-manager failures."""


class UnknownSessionError(SessionError):
    def __init__(self) -> None:
        super().__init__("session is unknown")


class SessionExpiredError(SessionError):
    def __init__(self) -> None:
        super().__init__("session has expired")


class SessionCapacityError(SessionError):
    def __init__(self) -> None:
        super().__init__("session capacity is exhausted")


class SessionBusyError(SessionError):
    def __init__(self) -> None:
        super().__init__("session already has an active generation")


class SessionRateLimitError(SessionError):
    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("session message rate limit exceeded")
        self.retry_after_seconds = max(1, retry_after_seconds)


class InvalidSessionInputError(SessionError):
    def __init__(self) -> None:
        super().__init__("session input is invalid")


class GenerationConflictError(SessionError):
    def __init__(self) -> None:
        super().__init__("generation no longer owns the session")


class GenerationCancelledError(SessionError):
    def __init__(self) -> None:
        super().__init__("generation was cancelled")


class SessionCleanupError(SessionError):
    def __init__(self) -> None:
        super().__init__("session cleanup could not be confirmed")
