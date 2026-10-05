"""Contract checks for deterministic components used by the Compose test profile."""

from __future__ import annotations

import json

import numpy as np
import pytest
from app.config import AppEnvironment, Settings
from app.testing.embedding import deterministic_vector
from app.testing.inference_fake import CONTEXT_EVIDENCE_ANSWERS, EVIDENCE_IDS, app
from fastapi.testclient import TestClient
from pydantic import ValidationError


def test_synthetic_mode_is_rejected_outside_test_environment() -> None:
    with pytest.raises(ValidationError, match="permitted only in the test environment"):
        Settings(synthetic_test_mode=True)

    settings = Settings(app_env=AppEnvironment.TEST, synthetic_test_mode=True)
    assert settings.synthetic_test_mode is True


def test_deterministic_embedding_is_normalized_and_repeatable() -> None:
    first = deterministic_vector("synthetic question")
    second = deterministic_vector("synthetic question")

    assert first.shape == (384,)
    assert np.array_equal(first, second)
    assert float(np.linalg.norm(first)) == pytest.approx(1.0)


def test_inference_fake_requires_the_complete_evidence_chain_and_erases_slot() -> None:
    prompt = json.dumps(
        {"eligible_evidence": [{"evidence_id": evidence_id} for evidence_id in EVIDENCE_IDS]}
    )
    payload = {
        "messages": [
            {"role": "system", "content": "bounded fixture"},
            {"role": "user", "content": prompt},
        ]
    }
    with TestClient(app) as client:
        response = client.post("/v1/chat/completions", json=payload)
        cleanup = client.post("/slots/0?action=erase")

    assert response.status_code == 200
    content = response.json()["choices"][0]["message"]["content"]
    answer = json.loads(content)
    assert answer["outcome"] == "answer"
    assert len([segment for segment in answer["segments"] if segment["kind"] == "step"]) == 5
    assert cleanup.json() == {"id_slot": 0, "n_erased": 1}


@pytest.mark.parametrize(("evidence_id", "expected_text"), CONTEXT_EVIDENCE_ANSWERS.items())
def test_inference_fake_returns_only_the_selected_context_answer(
    evidence_id: str,
    expected_text: str,
) -> None:
    prompt = json.dumps({"eligible_evidence": [{"evidence_id": evidence_id}]})
    payload = {
        "messages": [
            {"role": "system", "content": "bounded fixture"},
            {"role": "user", "content": prompt},
        ]
    }

    with TestClient(app) as client:
        response = client.post("/v1/chat/completions", json=payload)

    assert response.status_code == 200
    answer = json.loads(response.json()["choices"][0]["message"]["content"])
    assert answer["outcome"] == "answer"
    assert answer["segments"] == [
        {
            "kind": "explanation",
            "text": expected_text,
            "evidence_ids": [evidence_id],
        }
    ]
