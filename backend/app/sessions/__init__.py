"""Memory-only chat sessions with exact expiry and cancellation semantics."""

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
from app.sessions.manager import SessionManager
from app.sessions.models import (
    CancellationHandle,
    ConversationMessage,
    CreatedSession,
    GenerationLease,
    MessageRole,
    RequestBuffer,
    SessionSnapshot,
)

__all__ = [
    "CancellationHandle",
    "ConversationMessage",
    "CreatedSession",
    "GenerationCancelledError",
    "GenerationConflictError",
    "GenerationLease",
    "InvalidSessionInputError",
    "MessageRole",
    "RequestBuffer",
    "SessionBusyError",
    "SessionCapacityError",
    "SessionCleanupError",
    "SessionExpiredError",
    "SessionManager",
    "SessionRateLimitError",
    "SessionSnapshot",
    "UnknownSessionError",
]
