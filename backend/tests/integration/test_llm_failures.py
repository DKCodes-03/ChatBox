"""Integration coverage for Gemini failures reaching the public safe response."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest
from app.api.processor import RequestMessageProcessor, RetrievalResult
from app.generation.gemini import GeminiAdapter
from app.generation.schemas import AnswerOutcome, ReasonCode
from app.retrieval.search import (
    EvidenceApplicability,
    RetrievalChannel,
    RetrievalScope,
    RetrievedEvidence,
)
from app.sessions import CancellationHandle, GenerationLease
from app.sessions.models import ConversationMessage, MessageRole, RequestBuffer
from bs4 import BeautifulSoup
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import SecretStr

pytestmark = pytest.mark.integration

FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "corpus" / "us2"
PROVIDER_DETAIL_MARKER = "SYNTHETIC-PROVIDER-DETAIL-MUST-NOT-LEAK"


def _load_json(name: str) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8")))


PROVIDER_CASES = _load_json("provider-failures.json")["cases"]
METADATA = _load_json("fixture-metadata.json")
SOURCE = next(
    source
    for source in METADATA["sources"]
    if source["applicability"]["topic"] == "synthetic_personal_registration"
)
QUESTION = "What does the synthetic registration source say?"


@dataclass
class _Retriever:
    result: RetrievalResult

    def retrieve(
        self,
        *,
        query: str,
        context: Mapping[str, str],
    ) -> RetrievalResult:
        assert query == QUESTION
        assert context == {}
        return self.result


class _Response:
    def __init__(self, parsed: object) -> None:
        self._parsed = parsed

    @property
    def parsed(self) -> object:
        return self._parsed


class _Models:
    def __init__(self, failure_kind: str, response_shape: object | None) -> None:
        self.failure_kind = failure_kind
        self.response_shape = response_shape
        self.calls: list[dict[str, object]] = []

    async def generate_content(
        self,
        *,
        model: str,
        contents: str,
        config: types.GenerateContentConfig,
    ) -> _Response:
        self.calls.append({"model": model, "contents": contents, "config": config})
        if self.failure_kind == "quota":
            raise genai_errors.ClientError(
                429,
                {
                    "error": {
                        "code": 429,
                        "status": "RESOURCE_EXHAUSTED",
                        "message": PROVIDER_DETAIL_MARKER,
                    }
                },
            )
        if self.failure_kind == "timeout":
            raise TimeoutError(PROVIDER_DETAIL_MARKER)
        return _Response(self.response_shape)


class _AsyncClient:
    def __init__(self, models: _Models) -> None:
        self.models = models
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


class _Client:
    def __init__(self, async_client: _AsyncClient) -> None:
        self.aio = async_client


def _retrieved_evidence() -> RetrievedEvidence:
    item = SOURCE["evidence"][0]
    soup = BeautifulSoup(
        (FIXTURE_DIR / SOURCE["fixture_file"]).read_text(encoding="utf-8"),
        "html.parser",
    )
    section = soup.find(id=item["anchor"])
    assert section is not None
    return RetrievedEvidence(
        evidence_id=UUID(item["evidence_id"]),
        version_id=UUID(SOURCE["version_id"]),
        source_id=UUID(SOURCE["source_id"]),
        source_title=SOURCE["title"],
        canonical_url=SOURCE["url"],
        ordinal=item["ordinal"],
        heading_path=tuple(item["heading_path"]),
        page=None,
        anchor=item["anchor"],
        text=" ".join(section.get_text(" ", strip=True).split()),
        structured_content={},
        topic_key=item["topic_key"],
        scope={},
        applicability=EvidenceApplicability(**SOURCE["applicability"]),
        model_revision="fixture-minilm-v1",
        channel=RetrievalChannel.FULL_TEXT,
        rank=1,
        ranking_value=1.0,
    )


def _lease() -> GenerationLease:
    return GenerationLease(
        token=SecretStr("synthetic-us2-provider-session-token"),
        generation=1,
        cancellation=CancellationHandle(),
        buffer=RequestBuffer(
            [ConversationMessage(MessageRole.STUDENT, QUESTION)],
            {},
        ),
    )


@pytest.mark.parametrize("provider_case", PROVIDER_CASES, ids=lambda item: item["id"])
async def test_gemini_failure_is_sanitized_without_retry_or_partial_output(
    provider_case: dict[str, Any],
) -> None:
    evidence = _retrieved_evidence()
    scope = RetrievalScope(
        topic=evidence.topic_key,
        institution=SOURCE["applicability"]["institution"],
    )
    models = _Models(
        provider_case["failure_kind"],
        provider_case.get("response_shape"),
    )
    async_client = _AsyncClient(models)
    client = _Client(async_client)
    factory_calls: list[dict[str, object]] = []

    def client_factory(*, api_key: str, http_options: types.HttpOptions) -> _Client:
        factory_calls.append({"api_key": api_key, "http_options": http_options})
        return client

    adapter = GeminiAdapter(
        api_key_loader=lambda: SecretStr("synthetic-gemini-key"),
        client_factory=client_factory,
    )
    processor = RequestMessageProcessor(
        retriever=_Retriever(RetrievalResult(evidence=(evidence,), scope=scope)),
        primary=adapter,
    )

    result = await processor.process(_lease())

    assert result.outcome is AnswerOutcome.UNABLE
    assert result.reason_code == ReasonCode(provider_case["expected_reason_code"])
    assert result.citations == ()
    assert result.referral is None
    assert len(factory_calls) == 1
    assert len(models.calls) == 1
    assert async_client.closed is True
    serialized = result.model_dump_json()
    assert PROVIDER_DETAIL_MARKER not in serialized
    malformed_marker = provider_case.get("response_shape", {}).get("unexpected")
    if malformed_marker is not None:
        assert malformed_marker not in serialized
