"""Provider adapters must remain stateless and isolated from application persistence."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import cast

import pytest
from app.generation.adapter import GenerationRequest
from app.generation.gemini import GeminiAdapter
from app.generation.schemas import (
    AnswerOutcome,
    AnswerSegment,
    ReasonCode,
    SegmentKind,
    StructuredAnswer,
)
from app.sessions.models import CancellationHandle
from google.genai import types
from pydantic import SecretStr

BACKEND_ROOT = Path(__file__).resolve().parents[2]
PROVIDER_MODULES = (
    BACKEND_ROOT / "app/generation/adapter.py",
    BACKEND_ROOT / "app/generation/gemini.py",
    BACKEND_ROOT / "app/generation/local.py",
)
FORBIDDEN_IMPORT_PREFIXES = (
    "sqlalchemy",
    "app.models",
    "app.retrieval",
    "app.sessions.manager",
)


class _Response:
    def __init__(self, parsed: StructuredAnswer) -> None:
        self._parsed = parsed

    @property
    def parsed(self) -> object:
        return self._parsed


class _Models:
    def __init__(self, response: _Response) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    async def generate_content(
        self,
        *,
        model: str,
        contents: str,
        config: types.GenerateContentConfig,
    ) -> _Response:
        self.calls.append({"model": model, "contents": contents, "config": config})
        return self.response


class _AsyncClient:
    def __init__(self, models: _Models) -> None:
        self.models = models
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


class _Client:
    def __init__(self, async_client: _AsyncClient) -> None:
        self.aio = async_client


def _safe_answer() -> StructuredAnswer:
    return StructuredAnswer(
        outcome=AnswerOutcome.UNABLE,
        segments=(
            AnswerSegment(
                kind=SegmentKind.LIMITATION,
                text="I cannot provide a reliable answer.",
                evidence_ids=(),
            ),
        ),
        clarification=None,
        reason_code=ReasonCode.MISSING_EVIDENCE,
    )


def _imports(path: Path) -> frozenset[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
    return frozenset(imported)


def test_provider_modules_do_not_import_database_models_or_retrieval_state() -> None:
    for module in PROVIDER_MODULES:
        imported = _imports(module)
        assert all(
            not name.startswith(forbidden)
            for name in imported
            for forbidden in FORBIDDEN_IMPORT_PREFIXES
        ), f"{module.name} crosses the provider isolation boundary"


@pytest.mark.asyncio
async def test_gemini_call_is_stateless_bounded_and_closes_its_client() -> None:
    models = _Models(_Response(_safe_answer()))
    async_client = _AsyncClient(models)
    client = _Client(async_client)
    factory_calls: list[dict[str, object]] = []

    def factory(*, api_key: str, http_options: types.HttpOptions) -> _Client:
        factory_calls.append({"api_key": api_key, "http_options": http_options})
        return client

    adapter = GeminiAdapter(
        api_key_loader=lambda: SecretStr("synthetic-api-key"),
        client_factory=factory,
    )
    request = GenerationRequest(
        system_instruction="Use only supplied evidence.",
        prompt="Synthetic student question and evidence.",
    )

    result = await adapter.generate(request, cancellation=CancellationHandle())

    assert result == _safe_answer()
    assert len(factory_calls) == 1
    assert factory_calls[0]["api_key"] == "synthetic-api-key"
    assert len(models.calls) == 1
    call = models.calls[0]
    assert call["model"] == "gemini-2.5-flash-lite"
    assert call["contents"] == request.prompt
    config = cast(types.GenerateContentConfig, call["config"])
    assert config.candidate_count == 1
    assert config.max_output_tokens == 512
    assert config.cached_content is None
    assert config.tools is None
    assert config.system_instruction == request.system_instruction
    assert async_client.closed


def test_generation_request_repr_redacts_all_content() -> None:
    marker = "distinctive-private-marker"
    request = GenerationRequest(system_instruction=f"system-{marker}", prompt=f"prompt-{marker}")

    assert marker not in repr(request)
