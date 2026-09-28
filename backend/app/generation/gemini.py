"""Stateless Gemini structured generation with per-request secret loading."""

from __future__ import annotations

import asyncio
import weakref
from collections.abc import Callable
from contextlib import suppress
from typing import Protocol, cast

from app.config import Settings
from app.generation.adapter import GenerationCancellation, GenerationRequest
from app.generation.schemas import StructuredAnswer
from google import genai
from google.genai import types
from pydantic import SecretStr

GEMINI_MODEL = "gemini-2.5-flash-lite"
MAX_OUTPUT_TOKENS = 512
RESPONSE_MIME_TYPE = "application/json"

ApiKeyLoader = Callable[[], SecretStr]


class _GeminiResponse(Protocol):
    @property
    def parsed(self) -> object: ...


class _GeminiModels(Protocol):
    async def generate_content(
        self,
        *,
        model: str,
        contents: str,
        config: types.GenerateContentConfig,
    ) -> _GeminiResponse: ...


class _GeminiAsyncClient(Protocol):
    @property
    def models(self) -> _GeminiModels: ...

    async def aclose(self) -> None: ...


class _GeminiClient(Protocol):
    @property
    def aio(self) -> _GeminiAsyncClient: ...


class GeminiClientFactory(Protocol):
    def __call__(
        self,
        *,
        api_key: str,
        http_options: types.HttpOptions,
    ) -> _GeminiClient: ...


def _create_client(*, api_key: str, http_options: types.HttpOptions) -> _GeminiClient:
    client = genai.Client(api_key=api_key, http_options=http_options)
    return cast(_GeminiClient, client)


class GeminiAdapter:
    """Call Gemini without conversation IDs, history, tools, streaming, or caches."""

    def __init__(
        self,
        *,
        api_key_loader: ApiKeyLoader,
        model: str = GEMINI_MODEL,
        max_output_tokens: int = MAX_OUTPUT_TOKENS,
        timeout_seconds: float = 9.0,
        client_factory: GeminiClientFactory = _create_client,
    ) -> None:
        if model != GEMINI_MODEL:
            raise ValueError("only the approved Gemini model is supported")
        if max_output_tokens != MAX_OUTPUT_TOKENS:
            raise ValueError("Gemini output must remain limited to 512 tokens")
        if timeout_seconds <= 0 or timeout_seconds > 9:
            raise ValueError("Gemini timeout must be between zero and nine seconds")

        self._api_key_loader = api_key_loader
        self._model = model
        self._max_output_tokens = max_output_tokens
        self._timeout_milliseconds = max(1, round(timeout_seconds * 1_000))
        self._client_factory = client_factory

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        client_factory: GeminiClientFactory = _create_client,
    ) -> GeminiAdapter:
        if settings.provider_conversation_ids_enabled or settings.provider_prompt_cache_enabled:
            raise ValueError("Gemini conversation state and prompt caching must remain disabled")
        return cls(
            api_key_loader=settings.gemini_api_key,
            model=settings.gemini_model,
            max_output_tokens=settings.gemini_max_output_tokens,
            timeout_seconds=settings.generation_timeout_seconds,
            client_factory=client_factory,
        )

    @property
    def model(self) -> str:
        return self._model

    @property
    def max_output_tokens(self) -> int:
        return self._max_output_tokens

    async def generate(
        self,
        request: GenerationRequest,
        *,
        cancellation: GenerationCancellation,
    ) -> StructuredAnswer:
        cancellation.raise_if_cancelled()

        secret = self._api_key_loader()
        api_key = secret.get_secret_value()
        client = self._client_factory(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=self._timeout_milliseconds),
        )
        api_key = ""
        secret = SecretStr("")
        async_client = client.aio

        call_task: asyncio.Task[_GeminiResponse] | None = None
        try:
            config = types.GenerateContentConfig(
                candidate_count=1,
                max_output_tokens=self._max_output_tokens,
                response_mime_type=RESPONSE_MIME_TYPE,
                response_schema=StructuredAnswer,
                system_instruction=request.system_instruction,
                tools=None,
                cached_content=None,
            )
            call_task = asyncio.create_task(
                async_client.models.generate_content(
                    model=self._model,
                    contents=request.prompt,
                    config=config,
                )
            )
            self._connect_cancellation(call_task, cancellation)
            if cancellation.cancelled:
                call_task.cancel()
            cancellation.raise_if_cancelled()
            response = await call_task
            cancellation.raise_if_cancelled()
            answer = StructuredAnswer.model_validate(response.parsed)
            cancellation.raise_if_cancelled()
            return answer
        finally:
            if call_task is not None:
                await self._cancel_or_drain(call_task)
            await async_client.aclose()

    @staticmethod
    def _connect_cancellation(
        call_task: asyncio.Task[_GeminiResponse],
        cancellation: GenerationCancellation,
    ) -> None:
        loop = asyncio.get_running_loop()
        task_reference = weakref.ref(call_task)

        def cancel_provider_call() -> None:
            task = task_reference()
            if task is not None and not task.done():
                loop.call_soon_threadsafe(task.cancel)

        cancellation.add_callback(cancel_provider_call)

    @staticmethod
    async def _cancel_or_drain(call_task: asyncio.Task[_GeminiResponse]) -> None:
        if not call_task.done():
            call_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await call_task
            return

        with suppress(asyncio.CancelledError):
            call_task.exception()
