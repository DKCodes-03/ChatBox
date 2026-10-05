"""Operator CLI command, output, evaluation, and privacy-boundary tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path
from uuid import UUID

from app.api.schemas import AnswerEnvelope, AnswerSegment, Context
from app.cli import (
    EXIT_DEPENDENCY_FAILURE,
    EXIT_INVALID_INPUT,
    EXIT_PRECONDITION,
    CommandResult,
    EvaluationObservation,
    EvaluationService,
    main,
)
from app.config import Settings
from app.generation.schemas import AnswerOutcome, ReasonCode, SegmentKind

SOURCE_ID = UUID("10000000-0000-4000-8000-000000000001")
VERSION_ID = UUID("20000000-0000-4000-8000-000000000001")
QUALIFICATION_ID = UUID("30000000-0000-4000-8000-000000000001")
NOW = datetime(2030, 1, 15, 12, 0, tzinfo=UTC)


class _SourceCommands:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def inspect(self, source_id: UUID) -> CommandResult:
        self.calls.append(("inspect", source_id))
        return CommandResult(
            {
                "status": "eligible",
                "source_id": str(source_id),
                "version_id": str(VERSION_ID),
                "qualification_id": str(QUALIFICATION_ID),
                "reason_codes": [],
            }
        )

    def qualify(self, source_id: UUID) -> CommandResult:
        self.calls.append(("qualify", source_id))
        return CommandResult(
            {
                "status": "quarantined",
                "source_id": str(source_id),
                "version_id": str(VERSION_ID),
                "qualification_id": str(QUALIFICATION_ID),
                "reason_codes": ["evidence_incomplete"],
            },
            EXIT_PRECONDITION,
        )

    def withdraw(self, source_id: UUID, reason_code: str) -> CommandResult:
        self.calls.append(("withdraw", source_id, reason_code))
        return CommandResult(
            {
                "status": "withdrawn",
                "source_id": str(source_id),
                "reason_codes": [reason_code],
            }
        )

    def restore(self, source_id: UUID) -> CommandResult:
        self.calls.append(("restore", source_id))
        return CommandResult(
            {
                "status": "stale",
                "source_id": str(source_id),
                "reason_codes": ["restoration_requires_requalification"],
            }
        )

    def refresh_due(self) -> CommandResult:
        self.calls.append(("refresh-due",))
        return CommandResult(
            {
                "status": "success",
                "reason_codes": [],
                "source_ids": [str(SOURCE_ID)],
                "expired_source_ids": [],
            }
        )


def _run_source(*arguments: str) -> tuple[int, dict[str, object], _SourceCommands]:
    commands = _SourceCommands()
    output = StringIO()
    exit_code = main(
        ("sources", *arguments),
        source_operator=commands,
        stdout=output,
    )
    return exit_code, json.loads(output.getvalue()), commands


def _unable_answer() -> AnswerEnvelope:
    return AnswerEnvelope(
        outcome=AnswerOutcome.UNABLE,
        segments=(
            AnswerSegment(
                kind=SegmentKind.LIMITATION,
                text="I cannot provide a reliable answer from the available sources.",
                citation_ids=(),
            ),
        ),
        citations=(),
        context=Context(campus="Hammond"),
        clarification=None,
        referral=None,
        reason_code=ReasonCode.MISSING_EVIDENCE,
        expires_at=NOW + timedelta(minutes=30),
        server_time=NOW,
    )


def _write_cases(path: Path, cases: list[dict[str, object]]) -> None:
    path.write_text(json.dumps({"cases": cases}), encoding="utf-8")


def test_source_commands_emit_contract_json_and_preserve_failure_exit_code() -> None:
    inspect_exit, inspect_payload, commands = _run_source("inspect", "--source-id", str(SOURCE_ID))
    qualify_exit, qualify_payload, _ = _run_source("qualify", "--source-id", str(SOURCE_ID))

    assert inspect_exit == 0
    assert inspect_payload == {
        "status": "eligible",
        "source_id": str(SOURCE_ID),
        "version_id": str(VERSION_ID),
        "qualification_id": str(QUALIFICATION_ID),
        "reason_codes": [],
    }
    assert commands.calls == [("inspect", SOURCE_ID)]
    assert qualify_exit == EXIT_PRECONDITION
    assert qualify_payload["reason_codes"] == ["evidence_incomplete"]


def test_withdraw_restore_and_refresh_due_dispatch_exact_arguments() -> None:
    withdraw_exit, _, withdraw_commands = _run_source(
        "withdraw",
        "--source-id",
        str(SOURCE_ID),
        "--reason",
        "policy_removed",
    )
    restore_exit, _, restore_commands = _run_source("restore", "--source-id", str(SOURCE_ID))
    refresh_exit, refresh_payload, refresh_commands = _run_source("refresh-due")

    assert withdraw_exit == restore_exit == refresh_exit == 0
    assert withdraw_commands.calls == [("withdraw", SOURCE_ID, "policy_removed")]
    assert restore_commands.calls == [("restore", SOURCE_ID)]
    assert refresh_commands.calls == [("refresh-due",)]
    assert refresh_payload["source_ids"] == [str(SOURCE_ID)]


def test_invalid_source_input_returns_exit_two_without_database_or_secret_access() -> None:
    output = StringIO()

    exit_code = main(
        ("sources", "inspect", "--source-id", "not-a-uuid"),
        stdout=output,
    )

    assert exit_code == EXIT_INVALID_INPUT
    assert json.loads(output.getvalue()) == {
        "status": "error",
        "reason_codes": ["source_id_invalid"],
    }


def test_invalid_withdraw_reason_is_rejected_before_dispatch() -> None:
    exit_code, payload, commands = _run_source(
        "withdraw",
        "--source-id",
        str(SOURCE_ID),
        "--reason",
        "Contains free-form text",
    )

    assert exit_code == EXIT_INVALID_INPUT
    assert payload["reason_codes"] == ["reason_code_invalid"]
    assert commands.calls == []


def test_evaluation_writes_human_scoring_report_without_copying_questions(
    tmp_path: Path,
) -> None:
    cases_path = tmp_path / "cases.json"
    output_path = tmp_path / "report.json"
    question = "What should a synthetic student do when evidence is missing?"
    _write_cases(
        cases_path,
        [
            {
                "id": "missing-evidence",
                "question": question,
                "context": {"campus": "Hammond"},
                "expected_outcome": "unable",
                "expected_reason_code": "missing_evidence",
                "expected_evidence_ids": [],
                "expected_claims": [],
            }
        ],
    )
    service = EvaluationService(
        Settings(),
        executor=lambda cases: (
            EvaluationObservation(
                case_id=cases[0].case_id,
                answer=_unable_answer(),
                elapsed_ms=125,
            ),
        ),
    )

    result = service.evaluate(cases_path, output_path)
    report_text = output_path.read_text(encoding="utf-8")
    report = json.loads(report_text)

    assert result.exit_code == 0
    assert report["summary"] == {
        "total_cases": 1,
        "processed_cases": 1,
        "failed_cases": 0,
        "outcome_matches": 1,
        "responses_under_10_seconds": 1,
        "human_scoring_required": 1,
    }
    assert report["results"][0]["expected_reason_match"] is True
    assert report["results"][0]["requires_human_scoring"] is True
    assert question not in report_text


def test_evaluation_rejects_transcript_shaped_input_before_execution(tmp_path: Path) -> None:
    cases_path = tmp_path / "cases.json"
    output_path = tmp_path / "report.json"
    _write_cases(
        cases_path,
        [
            {
                "id": "unsafe-import",
                "question": "Synthetic question",
                "context": {},
                "expected_outcome": "unable",
                "expected_claims": [],
                "messages": [{"role": "student", "content": "private"}],
            }
        ],
    )
    called = False

    def executor(_cases: object) -> tuple[()]:
        nonlocal called
        called = True
        return ()

    service = EvaluationService(Settings(), executor=executor)

    try:
        service.evaluate(cases_path, output_path)
    except Exception as error:
        assert getattr(error, "reason_code", None) == "student_transcript_import_prohibited"
    else:
        raise AssertionError("transcript-shaped input must be rejected")
    assert called is False
    assert not output_path.exists()


def test_evaluation_case_failure_writes_incomplete_report_and_returns_exit_five(
    tmp_path: Path,
) -> None:
    cases_path = tmp_path / "cases.json"
    output_path = tmp_path / "report.json"
    _write_cases(
        cases_path,
        [
            {
                "id": "dependency-failure",
                "question": "Synthetic question",
                "context": {},
                "expected_outcome": "unable",
                "expected_claims": [],
            }
        ],
    )
    service = EvaluationService(
        Settings(),
        executor=lambda cases: (
            EvaluationObservation(
                case_id=cases[0].case_id,
                answer=None,
                elapsed_ms=50,
                error_reason="processing_unavailable",
            ),
        ),
    )

    result = service.evaluate(cases_path, output_path)
    report = json.loads(output_path.read_text(encoding="utf-8"))

    assert result.exit_code == EXIT_DEPENDENCY_FAILURE
    assert result.payload["reason_codes"] == ["evaluation_case_failed"]
    assert report["status"] == "incomplete"
    assert report["summary"]["failed_cases"] == 1
