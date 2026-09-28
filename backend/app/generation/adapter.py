"""Provider-neutral interface for stateless structured answer generation."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Protocol

from app.generation.schemas import StructuredAnswer
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

NonBlankPrompt = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
CancelCallback = Callable[[], None]


class GenerationRequest(BaseModel):
    """Transient prompt data; repr deliberately excludes all content-bearing fields."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        str_strip_whitespace=True,
    )

    system_instruction: NonBlankPrompt = Field(repr=False)
    prompt: NonBlankPrompt = Field(repr=False)


class GenerationCancellation(Protocol):
    """Cancellation operations required from the session-owned request handle."""

    @property
    def cancelled(self) -> bool: ...

    def raise_if_cancelled(self) -> None: ...

    def add_callback(self, callback: CancelCallback) -> None: ...


class LLMAdapter(Protocol):
    """Contract implemented by hosted and local stateless generation providers."""

    async def generate(
        self,
        request: GenerationRequest,
        *,
        cancellation: GenerationCancellation,
    ) -> StructuredAnswer:
        """Return a structured candidate without authorizing it for student display."""
        ...
