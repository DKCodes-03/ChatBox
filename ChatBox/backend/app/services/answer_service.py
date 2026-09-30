from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

import httpx

from app.core.config import get_settings


class DraftSupportError(ValueError):
    """Raised when a model draft claims facts that are not supported by the retrieved evidence."""


class AnswerService:
    """Draft concise answers from retrieved evidence while bounding the model to that evidence only."""

    DEFAULT_MODEL = "llama3.2:3b"
    _STOPWORDS = {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "can",
        "for",
        "from",
        "has",
        "have",
        "how",
        "in",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "the",
        "that",
        "this",
        "to",
        "use",
        "we",
        "with",
        "you",
        "your",
    }

    def __init__(
        self,
        model_name: str | None = None,
        *,
        base_url: str | None = None,
        timeout: float | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        settings = get_settings()
        self.model_name = model_name or self.DEFAULT_MODEL
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=(base_url or settings.ollama_base_url).rstrip("/"),
            timeout=timeout or 30.0,
        )

    def build_prompt(
        self,
        question: str,
        evidence: Sequence[Mapping[str, Any] | Any] | Any | None,
        *,
        scope: Mapping[str, str | bool | None] | None = None,
    ) -> str:
        cleaned_question = question.strip()
        if not cleaned_question:
            raise ValueError("question must not be empty")

        evidence_lines = self._evidence_lines(evidence)
        if not evidence_lines:
            raise ValueError("evidence must not be empty")

        scope_text = self._format_scope(scope)
        evidence_block = "\n".join(f"- {entry}" for entry in evidence_lines)
        return (
            "You are drafting a grounded answer for a Purdue University Northwest student question. "
            "Use only the provided evidence. Do not use outside knowledge or assume facts not stated in the evidence. "
            "If the evidence does not answer the question, say that the available evidence is insufficient.\n\n"
            f"Question: {cleaned_question}\n"
            f"Scope: {scope_text}\n\n"
            "Evidence:\n"
            f"{evidence_block}\n\n"
            "Return a concise answer in 2-4 sentences. Keep only facts that appear in the evidence and cite the relevant details from the evidence."
        )

    async def draft_answer(
        self,
        question: str,
        evidence: Sequence[Mapping[str, Any] | Any] | Any | None,
        *,
        scope: Mapping[str, str | bool | None] | None = None,
        model_name: str | None = None,
        temperature: float = 0.0,
    ) -> str:
        prompt = self.build_prompt(question, evidence, scope=scope)
        payload = {
            "model": model_name or self.model_name,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": float(temperature)},
        }

        try:
            response = await self._client.post("/api/generate", json=payload)
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise RuntimeError("Ollama answer generation failed") from exc

        raw_response = data.get("response") if isinstance(data, dict) else None
        if not isinstance(raw_response, str) or not raw_response.strip():
            raise RuntimeError("Ollama returned an empty answer draft")

        draft = raw_response.strip()
        self.validate_support(question, draft, evidence)
        return draft

    def validate_support(
        self,
        question: str,
        draft: str,
        evidence: Sequence[Mapping[str, Any] | Any] | Any | None,
    ) -> bool:
        if not isinstance(draft, str) or not draft.strip():
            raise DraftSupportError("draft answer must not be empty")

        evidence_text = self._evidence_text(evidence)
        if not evidence_text:
            raise DraftSupportError("answer validation requires non-empty evidence")

        sentences = [segment.strip() for segment in re.split(r"(?<=[.!?])\s+", draft.strip()) if segment.strip()]
        if not sentences:
            raise DraftSupportError("draft answer is missing a usable sentence")

        for sentence in sentences:
            if self._sentence_is_safe_fallback(sentence):
                continue
            if not self._sentence_supported_by_evidence(sentence, evidence_text):
                raise DraftSupportError(
                    f"draft answer contains unsupported information: {sentence}"
                )
        return True

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> "AnswerService":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    @staticmethod
    def _format_scope(scope: Mapping[str, str | bool | None] | None) -> str:
        if not scope:
            return "none provided"
        items = []
        for key, value in scope.items():
            if value is None:
                continue
            items.append(f"{key}={value}")
        return ", ".join(items) if items else "none provided"

    @staticmethod
    def _normalize_text(value: str) -> str:
        return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", value.lower())).strip()

    @classmethod
    def _content_tokens(cls, value: str) -> set[str]:
        tokens = cls._normalize_text(value).split()
        return {token for token in tokens if token and token not in cls._STOPWORDS and len(token) > 2}

    @classmethod
    def _sentence_is_safe_fallback(cls, sentence: str) -> bool:
        lowered = sentence.lower()
        markers = (
            "insufficient evidence",
            "not enough evidence",
            "cannot determine",
            "unable to confirm",
            "not provided",
            "not stated in the evidence",
            "the evidence does not say",
            "the available evidence is insufficient",
        )
        return any(marker in lowered for marker in markers)

    @classmethod
    def _sentence_supported_by_evidence(cls, sentence: str, evidence_text: str) -> bool:
        lowered_sentence = sentence.lower()
        if cls._sentence_is_safe_fallback(sentence):
            return True
        if any(marker in lowered_sentence for marker in ("i think", "i believe", "according to my knowledge", "as an ai")):
            return False

        sentence_tokens = cls._content_tokens(sentence)
        evidence_tokens = cls._content_tokens(evidence_text)
        if not sentence_tokens:
            return True

        if sentence_tokens & evidence_tokens:
            return True

        # Reject explicit fact claims in the absence of evidence overlap.
        if re.search(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b|\b\d{4}\b", sentence.lower()):
            return False

        return False

    @classmethod
    def _evidence_text(cls, evidence: Sequence[Mapping[str, Any] | Any] | Any | None) -> str:
        return "\n".join(part for part in cls._evidence_lines(evidence) if part)

    @classmethod
    def _flatten_value(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value.strip()] if value.strip() else []
        if isinstance(value, Mapping):
            items: list[str] = []
            for key in ("content", "text", "body", "snippet", "summary", "quote", "passage"):
                if key in value:
                    text = str(value[key]).strip()
                    if text:
                        items.append(text)
                if len(items) >= 3:
                    break
            if items:
                return items
            return [str(item).strip() for item in value.values() if str(item).strip()]
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            flattened: list[str] = []
            for item in value:
                flattened.extend(cls._flatten_value(item))
            return flattened
        return [str(value).strip()] if str(value).strip() else []

    @classmethod
    def _evidence_lines(cls, evidence: Sequence[Mapping[str, Any] | Any] | Any | None) -> list[str]:
        if evidence is None:
            return []
        if isinstance(evidence, Mapping):
            evidence = [evidence]
        elif isinstance(evidence, (str, bytes, bytearray)):
            evidence = [evidence]
        elif not isinstance(evidence, Sequence):
            evidence = [evidence]

        lines: list[str] = []
        for entry in evidence:
            if entry is None:
                continue
            if isinstance(entry, Mapping):
                title = entry.get("title")
                content_values = cls._flatten_value(entry)
                if title is not None and str(title).strip():
                    title_text = str(title).strip()
                    if content_values:
                        lines.append(f"{title_text}: {' '.join(content_values[:2])}")
                    else:
                        lines.append(title_text)
                else:
                    for value in content_values:
                        if value and value not in lines:
                            lines.append(value)
                continue

            for value in cls._flatten_value(entry):
                if value and value not in lines:
                    lines.append(value)
        return lines


def draft_answer(
    question: str,
    evidence: Sequence[Mapping[str, Any] | Any] | Any | None,
    *,
    scope: Mapping[str, str | bool | None] | None = None,
    model_name: str | None = None,
    temperature: float = 0.0,
    client: httpx.AsyncClient | None = None,
) -> Any:
    service = AnswerService(model_name=model_name, client=client)
    return service.draft_answer(
        question,
        evidence,
        scope=scope,
        model_name=model_name,
        temperature=temperature,
    )


def validate_answer_support(
    question: str,
    draft: str,
    evidence: Sequence[Mapping[str, Any] | Any] | Any | None,
) -> bool:
    return AnswerService().validate_support(question, draft, evidence)


__all__ = [
    "AnswerService",
    "DraftSupportError",
    "draft_answer",
    "validate_answer_support",
]
