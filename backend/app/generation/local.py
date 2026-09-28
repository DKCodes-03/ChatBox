"""Stateless llama.cpp generation with exclusive slots and verified cleanup."""

from __future__ import annotations

import asyncio
import threading
import weakref
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Protocol, cast

import httpx
from app.config import Settings
from app.generation.adapter import GenerationCancellation, GenerationRequest
from app.generation.schemas import StructuredAnswer
from pydantic import ValidationError

MAX_OUTPUT_TOKENS = 512
DEFAULT_SLOT_COUNT = 1

SlotQuarantineHook = Callable[[int], None]
RestartRequiredHook = Callable[[], None]


class LocalInferenceError(RuntimeError):
    """Base class for local-provider failures with content-free messages."""


class LocalInferenceUnavailableError(LocalInferenceError):
    def __init__(self) -> None:
        super().__init__("local inference is unavailable until the provider restarts")


class LocalInferenceRequestError(LocalInferenceError):
    def __init__(self) -> None:
        super().__init__("local inference request failed")


class InvalidLocalInferenceResponseError(LocalInferenceError):
    def __init__(self) -> None:
        super().__init__("local inference returned an invalid structured response")


class LocalInferenceCleanupError(LocalInferenceError):
    def __init__(self) -> None:
        super().__init__("local inference cleanup could not be confirmed")


class _LocalResponse(Protocol):
    def raise_for_status(self) -> None: ...

    def json(self) -> object: ...


class _LocalClient(Protocol):
    async def post(
        self,
        url: str,
        *,
        json: object | None = None,
    ) -> _LocalResponse: ...

    async def aclose(self) -> None: ...


class LocalClientFactory(Protocol):
    def __call__(
        self,
        *,
        base_url: str,
        timeout_seconds: float,
    ) -> _LocalClient: ...


def _create_client(*, base_url: str, timeout_seconds: float) -> _LocalClient:
    client = httpx.AsyncClient(
        base_url=base_url,
        timeout=httpx.Timeout(timeout_seconds),
        follow_redirects=False,
        trust_env=False,
        headers={"Accept": "application/json"},
    )
    return cast(_LocalClient, client)


class LocalLlamaAdapter:
    """Use one llama.cpp slot at a time and erase it after every attempted call."""

    def __init__(
        self,
        *,
        base_url: str,
        model_path: Path,
        max_output_tokens: int = MAX_OUTPUT_TOKENS,
        timeout_seconds: float = 9.0,
        slot_count: int = DEFAULT_SLOT_COUNT,
        client_factory: LocalClientFactory = _create_client,
        quarantine_hook: SlotQuarantineHook | None = None,
        restart_required_hook: RestartRequiredHook | None = None,
    ) -> None:
        normalized_base_url = self._validate_base_url(base_url)
        if not model_path.is_absolute():
            raise ValueError("local inference model path must be absolute")
        if max_output_tokens != MAX_OUTPUT_TOKENS:
            raise ValueError("local inference output must remain limited to 512 tokens")
        if timeout_seconds <= 0 or timeout_seconds > 9:
            raise ValueError("local inference timeout must be between zero and nine seconds")
        if slot_count < 1 or slot_count > 4:
            raise ValueError("local inference slot count must be between one and four")

        self._base_url = normalized_base_url
        self._model = str(model_path)
        self._max_output_tokens = max_output_tokens
        self._timeout_seconds = timeout_seconds
        self._client_factory = client_factory
        self._quarantine_hook = quarantine_hook
        self._restart_required_hook = restart_required_hook
        self._available_slots: asyncio.Queue[int] = asyncio.Queue(maxsize=slot_count)
        for slot_id in range(slot_count):
            self._available_slots.put_nowait(slot_id)
        self._state_lock = threading.Lock()
        self._quarantined_slots: set[int] = set()
        self._restart_required = False

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        client_factory: LocalClientFactory = _create_client,
        quarantine_hook: SlotQuarantineHook | None = None,
        restart_required_hook: RestartRequiredHook | None = None,
    ) -> LocalLlamaAdapter:
        if not settings.local_inference_enabled:
            raise ValueError("local inference must be explicitly enabled")
        if settings.provider_conversation_ids_enabled or settings.provider_prompt_cache_enabled:
            raise ValueError("local conversation state and prompt caching must remain disabled")
        return cls(
            base_url=str(settings.local_inference_base_url),
            model_path=settings.local_inference_model_path,
            max_output_tokens=settings.gemini_max_output_tokens,
            timeout_seconds=settings.generation_timeout_seconds,
            slot_count=DEFAULT_SLOT_COUNT,
            client_factory=client_factory,
            quarantine_hook=quarantine_hook,
            restart_required_hook=restart_required_hook,
        )

    @property
    def model(self) -> str:
        return self._model

    @property
    def max_output_tokens(self) -> int:
        return self._max_output_tokens

    @property
    def restart_required(self) -> bool:
        with self._state_lock:
            return self._restart_required

    @property
    def ready(self) -> bool:
        return not self.restart_required

    @property
    def quarantined_slots(self) -> frozenset[int]:
        with self._state_lock:
            return frozenset(self._quarantined_slots)

    async def generate(
        self,
        request: GenerationRequest,
        *,
        cancellation: GenerationCancellation,
    ) -> StructuredAnswer:
        """Return an untrusted structured candidate after its slot is erased."""

        self._ensure_ready()
        cancellation.raise_if_cancelled()
        slot_id = await self._acquire_slot(cancellation)
        client: _LocalClient | None = None
        call_task: asyncio.Task[_LocalResponse] | None = None
        slot_was_used = False

        try:
            try:
                client = self._client_factory(
                    base_url=self._base_url,
                    timeout_seconds=self._timeout_seconds,
                )
            except Exception:
                raise LocalInferenceRequestError() from None

            payload = self._request_payload(request, slot_id)
            slot_was_used = True
            call_task = asyncio.create_task(client.post("/v1/chat/completions", json=payload))
            self._connect_cancellation(call_task, cancellation)
            if cancellation.cancelled:
                call_task.cancel()
            cancellation.raise_if_cancelled()

            try:
                response = await call_task
            except asyncio.CancelledError:
                cancellation.raise_if_cancelled()
                raise
            except Exception:
                raise LocalInferenceRequestError() from None

            cancellation.raise_if_cancelled()
            try:
                response.raise_for_status()
            except Exception:
                raise LocalInferenceRequestError() from None
            answer = self._parse_answer(response)
            cancellation.raise_if_cancelled()
        finally:
            if call_task is not None:
                await self._cancel_or_drain(call_task)

            cleanup_was_cancelled = False
            cleanup_failed = False
            if slot_was_used and client is not None:
                try:
                    cleanup_was_cancelled = await self._erase_slot(client, slot_id)
                except asyncio.CancelledError:
                    self._quarantine(slot_id)
                    cleanup_failed = True
                    cleanup_was_cancelled = True
                except Exception:
                    self._quarantine(slot_id)
                    cleanup_failed = True

            if not cleanup_failed:
                self._available_slots.put_nowait(slot_id)
            if client is not None:
                with suppress(Exception):
                    await client.aclose()
            if cleanup_failed:
                if cleanup_was_cancelled:
                    raise asyncio.CancelledError
                raise LocalInferenceCleanupError() from None
            if cleanup_was_cancelled:
                raise asyncio.CancelledError

        self._ensure_ready()
        return answer

    def _request_payload(self, request: GenerationRequest, slot_id: int) -> dict[str, object]:
        return {
            "model": self._model,
            "messages": [
                {"role": "system", "content": request.system_instruction},
                {"role": "user", "content": request.prompt},
            ],
            "max_tokens": self._max_output_tokens,
            "n": 1,
            "stream": False,
            "cache_prompt": False,
            "id_slot": slot_id,
            "chat_template_kwargs": {"enable_thinking": False},
            "response_format": {
                "type": "json_schema",
                "schema": StructuredAnswer.model_json_schema(mode="validation"),
            },
        }

    @staticmethod
    def _parse_answer(response: _LocalResponse) -> StructuredAnswer:
        try:
            payload = response.json()
            if not isinstance(payload, dict):
                raise TypeError
            choices = payload.get("choices")
            if not isinstance(choices, list) or len(choices) != 1:
                raise TypeError
            choice = choices[0]
            if not isinstance(choice, dict):
                raise TypeError
            message = choice.get("message")
            if not isinstance(message, dict):
                raise TypeError
            content = message.get("content")
            if not isinstance(content, str) or not content:
                raise TypeError
            return StructuredAnswer.model_validate_json(content)
        except (TypeError, ValueError, ValidationError):
            raise InvalidLocalInferenceResponseError() from None

    async def _acquire_slot(self, cancellation: GenerationCancellation) -> int:
        self._ensure_ready()
        acquire_task = asyncio.create_task(self._available_slots.get())
        self._connect_cancellation(acquire_task, cancellation)
        if cancellation.cancelled:
            acquire_task.cancel()
        try:
            slot_id = await acquire_task
        except asyncio.CancelledError:
            cancellation.raise_if_cancelled()
            raise

        try:
            cancellation.raise_if_cancelled()
            self._ensure_ready()
        except BaseException:
            self._available_slots.put_nowait(slot_id)
            raise
        return slot_id

    async def _erase_slot(self, client: _LocalClient, slot_id: int) -> bool:
        """Confirm slot erasure and report whether outer cancellation was delayed."""

        cleanup_task = asyncio.create_task(self._confirm_slot_erased(client, slot_id))
        try:
            await asyncio.shield(cleanup_task)
            return False
        except asyncio.CancelledError:
            try:
                await cleanup_task
            except asyncio.CancelledError:
                raise LocalInferenceCleanupError() from None
            return True

    @staticmethod
    async def _confirm_slot_erased(client: _LocalClient, slot_id: int) -> None:
        try:
            response = await client.post(f"/slots/{slot_id}?action=erase")
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise TypeError
            erased_slot = payload.get("id_slot")
            erased_count = payload.get("n_erased")
            if (
                isinstance(erased_slot, bool)
                or erased_slot != slot_id
                or isinstance(erased_count, bool)
                or not isinstance(erased_count, int)
                or erased_count < 0
            ):
                raise ValueError
        except asyncio.CancelledError:
            raise
        except Exception:
            raise LocalInferenceCleanupError() from None

    def _quarantine(self, slot_id: int) -> None:
        with self._state_lock:
            newly_quarantined = slot_id not in self._quarantined_slots
            first_restart_request = not self._restart_required
            self._quarantined_slots.add(slot_id)
            self._restart_required = True

        if newly_quarantined and self._quarantine_hook is not None:
            with suppress(Exception):
                self._quarantine_hook(slot_id)
        if first_restart_request and self._restart_required_hook is not None:
            with suppress(Exception):
                self._restart_required_hook()

    def _ensure_ready(self) -> None:
        if self.restart_required:
            raise LocalInferenceUnavailableError()

    @staticmethod
    def _connect_cancellation(
        task: asyncio.Task[object],
        cancellation: GenerationCancellation,
    ) -> None:
        loop = asyncio.get_running_loop()
        task_reference = weakref.ref(task)

        def cancel_provider_work() -> None:
            active_task = task_reference()
            if active_task is not None and not active_task.done():
                loop.call_soon_threadsafe(active_task.cancel)

        cancellation.add_callback(cancel_provider_work)

    @staticmethod
    async def _cancel_or_drain(call_task: asyncio.Task[_LocalResponse]) -> None:
        if not call_task.done():
            call_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await call_task
            return

        with suppress(asyncio.CancelledError):
            call_task.exception()

    @staticmethod
    def _validate_base_url(base_url: str) -> str:
        try:
            parsed = httpx.URL(base_url)
        except Exception:
            raise ValueError("local inference base URL is invalid") from None
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.host is None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
        ):
            raise ValueError("local inference base URL must be an HTTP origin")
        return str(parsed).rstrip("/")
