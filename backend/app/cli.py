"""Controlled operator CLI for source governance and nonpersonal evaluation."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import IO, Never, Protocol, cast
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import Engine, create_engine, select
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.api.processor import build_runtime_message_processor
from app.api.schemas import AnswerEnvelope
from app.config import DatabaseRole, SecretFileError, Settings
from app.context import ContextValidationError, StudentContext, validate_context
from app.generation.schemas import AnswerOutcome, ReasonCode
from app.ingestion.discovery import (
    DiscoveredDocument,
    DiscoveryInputError,
    DiscoveryPolicy,
    SourceDiscovery,
    SourceSeed,
)
from app.ingestion.pipeline import (
    IngestionPipelineError,
    PipelineFailureReason,
    SourceIngestionPipeline,
)
from app.ingestion.qualification import (
    QualificationError,
    QualificationEvidence,
    QualificationPolicy,
    SourceQualificationService,
)
from app.ingestion.refresh import (
    RefreshError,
    RefreshFailureReason,
    RefreshInputError,
    SourceRefreshService,
)
from app.models.sources import Applicability, Qualification, Source, SourceVersion
from app.sessions import GenerationLease, SessionManager, SessionSnapshot

EXIT_SUCCESS = 0
EXIT_INVALID_INPUT = 2
EXIT_UNAUTHORIZED = 3
EXIT_PRECONDITION = 4
EXIT_DEPENDENCY_FAILURE = 5

MAX_EVALUATION_CASES = 500
MAX_EXPECTED_CLAIMS = 50
MAX_EXPECTED_CLAIM_CHARACTERS = 2_000
_REASON_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,127}$")
_CASE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_PROHIBITED_EVALUATION_KEYS = frozenset(
    {
        "conversation",
        "conversations",
        "messages",
        "student_id",
        "student_name",
        "transcript",
        "transcripts",
        "user_id",
    }
)


class CLIError(RuntimeError):
    """Sanitized command failure with a contract exit code and bounded reason."""

    def __init__(self, reason_code: str, exit_code: int) -> None:
        super().__init__(reason_code)
        self.reason_code = _bounded_reason(reason_code)
        self.exit_code = exit_code


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> Never:
        raise CLIError("invalid_arguments", EXIT_INVALID_INPUT)


@dataclass(frozen=True, slots=True)
class CommandResult:
    payload: Mapping[str, object]
    exit_code: int = EXIT_SUCCESS


@dataclass(frozen=True, slots=True)
class _QualificationTarget:
    source_id: UUID
    version_id: UUID
    canonical_url: str
    title: str
    topics: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _ObservedDocument:
    document: DiscoveredDocument
    observed_at: datetime


class SourceCommands(Protocol):
    def import_manifest(self, manifest_path: Path) -> CommandResult: ...

    def fetch(self, source_id: UUID) -> CommandResult: ...

    def inspect(self, source_id: UUID) -> CommandResult: ...

    def qualify(self, source_id: UUID) -> CommandResult: ...

    def withdraw(self, source_id: UUID, reason_code: str) -> CommandResult: ...

    def restore(self, source_id: UUID) -> CommandResult: ...

    def refresh_due(self) -> CommandResult: ...


class EvaluationCommands(Protocol):
    def evaluate(self, cases_path: Path, output_path: Path) -> CommandResult: ...


QualificationLoader = Callable[[_QualificationTarget], _ObservedDocument]
SessionFactory = sessionmaker[Session]


class SourceOperator:
    """Run source commands in short transactions owned by the governance role."""

    def __init__(
        self,
        engine: Engine,
        *,
        settings: Settings,
        qualification_loader: QualificationLoader | None = None,
        ingestion_pipeline: SourceIngestionPipeline | None = None,
    ) -> None:
        self._engine = engine
        self._settings = settings
        self._session_factory: SessionFactory = sessionmaker(
            bind=engine,
            expire_on_commit=False,
        )
        self._qualification_loader = qualification_loader or self._load_public_document
        self._ingestion_pipeline = ingestion_pipeline or SourceIngestionPipeline(
            engine,
            settings=settings,
        )

    def close(self) -> None:
        self._engine.dispose()

    def import_manifest(self, manifest_path: Path) -> CommandResult:
        result = self._ingestion_pipeline.import_manifest(manifest_path)
        return CommandResult(
            {
                "status": "success",
                "source_ids": [str(source_id) for source_id in result.source_ids],
                "created_source_ids": [str(source_id) for source_id in result.created_source_ids],
                "reason_codes": [],
            }
        )

    def fetch(self, source_id: UUID) -> CommandResult:
        result = self._ingestion_pipeline.fetch(source_id)
        requested = next(
            (item for item in result.documents if item.source_id == source_id),
            None,
        )
        payload: dict[str, object] = {
            "status": (requested.status.value if requested is not None else "incomplete"),
            "source_id": str(source_id),
            "reason_codes": list(result.reason_codes),
            "document_source_ids": [str(item.source_id) for item in result.documents],
            "truncated": result.truncated,
        }
        if requested is not None:
            payload["version_id"] = str(requested.version_id)
            payload["qualification_id"] = str(requested.qualification_id)
        return CommandResult(
            payload,
            EXIT_SUCCESS if result.successful else EXIT_PRECONDITION,
        )

    def inspect(self, source_id: UUID) -> CommandResult:
        with self._session_factory() as session:
            source = session.get(Source, source_id)
            if source is None:
                raise CLIError("source_not_found", EXIT_PRECONDITION)
            version = _latest_version(session, source.id)
            qualification = (
                _latest_qualification(session, version.id) if version is not None else None
            )
            reasons = (
                _qualification_reason_codes(qualification) if qualification is not None else ()
            )
            if version is None:
                reasons = ("version_missing",)
            elif qualification is None:
                reasons = ("qualification_missing",)
            return CommandResult(
                _source_payload(
                    status=source.status.value,
                    source_id=source.id,
                    version_id=version.id if version is not None else None,
                    qualification_id=(qualification.id if qualification is not None else None),
                    reason_codes=reasons,
                )
            )

    def qualify(self, source_id: UUID) -> CommandResult:
        target = self._qualification_target(source_id)
        try:
            observation = self._qualification_loader(target)
        except CLIError as error:
            if error.exit_code in (EXIT_PRECONDITION, EXIT_DEPENDENCY_FAILURE):
                return self._record_refresh_failure(
                    source_id,
                    error.reason_code,
                    exit_code=error.exit_code,
                )
            raise

        with self._session_factory.begin() as session:
            source = session.scalar(select(Source).where(Source.id == source_id).with_for_update())
            if source is None:
                raise CLIError("source_not_found", EXIT_PRECONDITION)
            version = _latest_version(session, source.id)
            if version is None:
                raise CLIError("version_missing", EXIT_PRECONDITION)
            if version.id != target.version_id:
                raise CLIError("source_changed_during_qualification", EXIT_PRECONDITION)
            if sha256(observation.document.content).hexdigest() != version.content_sha256:
                lifecycle = SourceRefreshService(session).record_refresh_failure(
                    source_id=source.id,
                    reason_code=RefreshFailureReason.MATERIAL_CHANGE_PENDING.value,
                )
                return CommandResult(
                    _source_payload(
                        status=lifecycle.status.value,
                        source_id=source.id,
                        version_id=version.id,
                        reason_codes=(lifecycle.reason_code,),
                    ),
                    EXIT_PRECONDITION,
                )
            result = SourceQualificationService(
                session,
                policy=QualificationPolicy.from_settings(self._settings),
            ).qualify(
                source=source,
                version=version,
                evidence=QualificationEvidence(
                    document=observation.document,
                    observed_at=observation.observed_at,
                ),
            )
            return CommandResult(
                _source_payload(
                    status=source.status.value,
                    source_id=source.id,
                    version_id=version.id,
                    qualification_id=result.qualification.id,
                    reason_codes=tuple(reason.value for reason in result.reason_codes),
                ),
                EXIT_SUCCESS if result.eligible else EXIT_PRECONDITION,
            )

    def withdraw(self, source_id: UUID, reason_code: str) -> CommandResult:
        reason = _bounded_reason(reason_code)
        with self._session_factory.begin() as session:
            result = SourceRefreshService(session).withdraw(
                source_id=source_id,
                reason_code=reason,
            )
            return CommandResult(
                _source_payload(
                    status=result.status.value,
                    source_id=result.source_id,
                    reason_codes=(result.reason_code,),
                )
            )

    def restore(self, source_id: UUID) -> CommandResult:
        with self._session_factory.begin() as session:
            result = SourceRefreshService(session).restore(source_id=source_id)
            return CommandResult(
                _source_payload(
                    status=result.status.value,
                    source_id=result.source_id,
                    reason_codes=(result.reason_code,),
                )
            )

    def refresh_due(self) -> CommandResult:
        result = self._ingestion_pipeline.refresh_due()
        exit_code = EXIT_PRECONDITION if result.failed_source_ids else EXIT_SUCCESS
        return CommandResult(
            {
                "status": "success" if exit_code == EXIT_SUCCESS else "incomplete",
                "reason_codes": list(result.reason_codes),
                "source_ids": [str(source_id) for source_id in result.source_ids],
                "refreshed_source_ids": [
                    str(source_id) for source_id in result.refreshed_source_ids
                ],
                "failed_source_ids": [str(source_id) for source_id in result.failed_source_ids],
                "expired_source_ids": [str(source_id) for source_id in result.expired_source_ids],
            },
            exit_code,
        )

    def _qualification_target(self, source_id: UUID) -> _QualificationTarget:
        with self._session_factory() as session:
            source = session.get(Source, source_id)
            if source is None:
                raise CLIError("source_not_found", EXIT_PRECONDITION)
            version = _latest_version(session, source.id)
            if version is None:
                raise CLIError("version_missing", EXIT_PRECONDITION)
            topics = tuple(
                session.scalars(
                    select(Applicability.topic)
                    .where(Applicability.version_id == version.id)
                    .distinct()
                    .order_by(Applicability.topic)
                ).all()
            )
            return _QualificationTarget(
                source_id=source.id,
                version_id=version.id,
                canonical_url=source.canonical_url,
                title=source.title,
                topics=topics or ("unclassified",),
            )

    def _record_refresh_failure(
        self,
        source_id: UUID,
        reason_code: str,
        *,
        exit_code: int = EXIT_DEPENDENCY_FAILURE,
    ) -> CommandResult:
        with self._session_factory.begin() as session:
            result = SourceRefreshService(session).record_refresh_failure(
                source_id=source_id,
                reason_code=reason_code,
            )
            return CommandResult(
                _source_payload(
                    status=result.status.value,
                    source_id=result.source_id,
                    reason_codes=(result.reason_code,),
                ),
                exit_code,
            )

    def _load_public_document(self, target: _QualificationTarget) -> _ObservedDocument:
        host = (urlsplit(target.canonical_url).hostname or "").lower().rstrip(".")
        try:
            policy = DiscoveryPolicy(
                allowed_schemes=self._settings.source_allowed_schemes,
                allowed_hosts=self._settings.source_allowed_hosts,
                max_crawl_depth=0,
                max_urls_per_run=1,
                max_document_bytes=self._settings.source_max_document_bytes,
            )
            seed = SourceSeed(
                url=target.canonical_url,
                title=target.title,
                topics=target.topics,
                allowed_child_hosts=(host,),
            )
            with SourceDiscovery(policy) as discovery:
                report = discovery.discover((seed,))
        except DiscoveryInputError:
            raise CLIError("source_configuration_invalid", EXIT_INVALID_INPUT) from None
        if not report.documents:
            reason = report.issues[0].reason.value if report.issues else "refresh_failed"
            raise CLIError(reason, EXIT_DEPENDENCY_FAILURE)
        document = next(
            (item for item in report.documents if item.canonical_url == target.canonical_url),
            None,
        )
        if document is None:
            raise CLIError("provenance_mismatch", EXIT_PRECONDITION)
        return _ObservedDocument(document=document, observed_at=datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class EvaluationCase:
    case_id: str
    question: str
    context: StudentContext
    expected_outcome: AnswerOutcome
    expected_reason_code: ReasonCode | None
    expected_evidence_ids: tuple[UUID, ...]
    expected_claims: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EvaluationObservation:
    case_id: str
    answer: AnswerEnvelope | None
    elapsed_ms: int
    error_reason: str | None = None


EvaluationExecutor = Callable[[Sequence[EvaluationCase]], Sequence[EvaluationObservation]]


class EvaluationService:
    """Execute bounded nonpersonal cases and write a report for human scoring."""

    def __init__(
        self,
        settings: Settings,
        *,
        executor: EvaluationExecutor | None = None,
    ) -> None:
        self._settings = settings
        self._executor = executor or self._run_runtime_cases

    def evaluate(self, cases_path: Path, output_path: Path) -> CommandResult:
        if _same_path(cases_path, output_path):
            raise CLIError("evaluation_paths_conflict", EXIT_INVALID_INPUT)
        cases = _load_evaluation_cases(cases_path)
        observations = tuple(self._executor(cases))
        if len(observations) != len(cases):
            raise CLIError("evaluation_result_incomplete", EXIT_DEPENDENCY_FAILURE)
        by_id = {item.case_id: item for item in observations}
        if len(by_id) != len(observations) or set(by_id) != {item.case_id for item in cases}:
            raise CLIError("evaluation_result_invalid", EXIT_DEPENDENCY_FAILURE)

        results = [_evaluation_result(case, by_id[case.case_id]) for case in cases]
        failed = sum(item["status"] == "error" for item in results)
        responses_under_ten_seconds = sum(
            item.get("status") == "processed"
            and isinstance(item.get("elapsed_ms"), int)
            and cast(int, item["elapsed_ms"]) < 10_000
            for item in results
        )
        report: dict[str, object] = {
            "status": "completed" if failed == 0 else "incomplete",
            "summary": {
                "total_cases": len(cases),
                "processed_cases": len(cases) - failed,
                "failed_cases": failed,
                "outcome_matches": sum(
                    item.get("expected_outcome_match") is True for item in results
                ),
                "responses_under_10_seconds": responses_under_ten_seconds,
                "human_scoring_required": len(cases) - failed,
            },
            "results": results,
        }
        _atomic_write_json(output_path, report)
        return CommandResult(
            {
                "status": report["status"],
                "reason_codes": [] if failed == 0 else ["evaluation_case_failed"],
                "output": str(output_path),
                "case_count": len(cases),
            },
            EXIT_SUCCESS if failed == 0 else EXIT_DEPENDENCY_FAILURE,
        )

    def _run_runtime_cases(
        self,
        cases: Sequence[EvaluationCase],
    ) -> Sequence[EvaluationObservation]:
        return asyncio.run(_execute_runtime_cases(self._settings, cases))


async def _execute_runtime_cases(
    settings: Settings,
    cases: Sequence[EvaluationCase],
) -> tuple[EvaluationObservation, ...]:
    processor = build_runtime_message_processor(settings)
    manager = SessionManager.from_settings(settings, auto_start=False)
    observations: list[EvaluationObservation] = []
    try:
        for case in cases:
            started = time.perf_counter()
            created = manager.create_session()
            token = created.token
            lease = manager.admit_student_message(
                token,
                case.question,
                context=case.context.as_mapping(),
            )
            try:

                def publish(
                    answer: AnswerEnvelope,
                    current_lease: GenerationLease = lease,
                ) -> SessionSnapshot:
                    message_parts = [segment.text for segment in answer.segments]
                    if answer.clarification is not None:
                        message_parts.append(answer.clarification.question)
                    return manager.complete_generation(
                        current_lease,
                        assistant_message="\n".join(message_parts),
                    )

                answer, _snapshot = await processor.process_and_publish(
                    lease,
                    publisher=publish,
                )
                observations.append(
                    EvaluationObservation(
                        case_id=case.case_id,
                        answer=answer,
                        elapsed_ms=max(0, round((time.perf_counter() - started) * 1_000)),
                    )
                )
            except Exception:
                manager.abandon_generation(lease)
                observations.append(
                    EvaluationObservation(
                        case_id=case.case_id,
                        answer=None,
                        elapsed_ms=max(0, round((time.perf_counter() - started) * 1_000)),
                        error_reason="processing_unavailable",
                    )
                )
            finally:
                manager.end_chat(token)
    finally:
        manager.shutdown()
        processor.close()
    return tuple(observations)


def _evaluation_result(
    case: EvaluationCase,
    observation: EvaluationObservation,
) -> dict[str, object]:
    if observation.answer is None:
        return {
            "case_id": case.case_id,
            "status": "error",
            "elapsed_ms": observation.elapsed_ms,
            "reason_code": observation.error_reason or "processing_unavailable",
            "requires_human_scoring": False,
        }
    answer = observation.answer
    citation_ids = tuple(UUID(item.id) for item in answer.citations)
    expected_ids = set(case.expected_evidence_ids)
    return {
        "case_id": case.case_id,
        "status": "processed",
        "elapsed_ms": observation.elapsed_ms,
        "observed_outcome": answer.outcome.value,
        "observed_reason_code": (
            answer.reason_code.value if answer.reason_code is not None else None
        ),
        "expected_outcome_match": answer.outcome is case.expected_outcome,
        "expected_reason_match": answer.reason_code is case.expected_reason_code,
        "expected_evidence_present": expected_ids.issubset(citation_ids),
        "segments": [
            {"kind": segment.kind.value, "text": segment.text} for segment in answer.segments
        ],
        "citations": [
            {
                "evidence_id": citation.id,
                "version_id": str(citation.version_id),
                "source_title": citation.source_title,
                "url": str(citation.url),
            }
            for citation in answer.citations
        ],
        "expected_claims": list(case.expected_claims),
        "requires_human_scoring": True,
    }


def _load_evaluation_cases(path: Path) -> tuple[EvaluationCase, ...]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise CLIError("evaluation_cases_unavailable", EXIT_INVALID_INPUT) from None
    if not isinstance(raw, dict) or set(raw) != {"cases"}:
        raise CLIError("evaluation_cases_invalid", EXIT_INVALID_INPUT)
    _reject_transcript_fields(raw)
    raw_cases = raw["cases"]
    if not isinstance(raw_cases, list) or not raw_cases or len(raw_cases) > MAX_EVALUATION_CASES:
        raise CLIError("evaluation_cases_invalid", EXIT_INVALID_INPUT)
    cases = tuple(_parse_evaluation_case(item) for item in raw_cases)
    if len({item.case_id for item in cases}) != len(cases):
        raise CLIError("evaluation_case_ids_duplicate", EXIT_INVALID_INPUT)
    return cases


def _parse_evaluation_case(raw: object) -> EvaluationCase:
    allowed = {
        "id",
        "question",
        "context",
        "expected_outcome",
        "expected_reason_code",
        "expected_evidence_ids",
        "expected_claims",
    }
    if not isinstance(raw, dict) or set(raw) - allowed:
        raise CLIError("evaluation_case_invalid", EXIT_INVALID_INPUT)
    case_id = raw.get("id")
    question = raw.get("question")
    if not isinstance(case_id, str) or _CASE_ID_RE.fullmatch(case_id) is None:
        raise CLIError("evaluation_case_id_invalid", EXIT_INVALID_INPUT)
    if (
        not isinstance(question, str)
        or not question.strip()
        or len(question) > 4_000
        or any(ord(character) < 32 and character not in "\n\t" for character in question)
    ):
        raise CLIError("evaluation_question_invalid", EXIT_INVALID_INPUT)
    try:
        context = validate_context(raw.get("context"))
        outcome_value = raw.get("expected_outcome")
        if not isinstance(outcome_value, str):
            raise ValueError
        expected_outcome = AnswerOutcome(outcome_value)
        reason_value = raw.get("expected_reason_code")
        expected_reason = ReasonCode(reason_value) if reason_value is not None else None
    except (ContextValidationError, TypeError, ValueError):
        raise CLIError("evaluation_expectation_invalid", EXIT_INVALID_INPUT) from None
    raw_ids = raw.get("expected_evidence_ids", [])
    raw_claims = raw.get("expected_claims", [])
    if not isinstance(raw_ids, list) or not all(isinstance(item, str) for item in raw_ids):
        raise CLIError("evaluation_expectation_invalid", EXIT_INVALID_INPUT)
    try:
        expected_ids = tuple(UUID(item) for item in raw_ids)
    except ValueError:
        raise CLIError("evaluation_expectation_invalid", EXIT_INVALID_INPUT) from None
    if len(expected_ids) != len(set(expected_ids)):
        raise CLIError("evaluation_expectation_invalid", EXIT_INVALID_INPUT)
    if (
        not isinstance(raw_claims, list)
        or len(raw_claims) > MAX_EXPECTED_CLAIMS
        or not all(
            isinstance(item, str)
            and bool(item.strip())
            and len(item) <= MAX_EXPECTED_CLAIM_CHARACTERS
            for item in raw_claims
        )
    ):
        raise CLIError("evaluation_expectation_invalid", EXIT_INVALID_INPUT)
    if expected_outcome in (AnswerOutcome.ANSWER, AnswerOutcome.PARTIAL) and not raw_claims:
        raise CLIError("evaluation_expected_claims_missing", EXIT_INVALID_INPUT)
    return EvaluationCase(
        case_id=case_id,
        question=question.strip(),
        context=context,
        expected_outcome=expected_outcome,
        expected_reason_code=expected_reason,
        expected_evidence_ids=expected_ids,
        expected_claims=tuple(item.strip() for item in raw_claims),
    )


def _reject_transcript_fields(value: object) -> None:
    if isinstance(value, dict):
        if any(str(key).casefold() in _PROHIBITED_EVALUATION_KEYS for key in value):
            raise CLIError("student_transcript_import_prohibited", EXIT_INVALID_INPUT)
        for item in value.values():
            _reject_transcript_fields(item)
    elif isinstance(value, list):
        for item in value:
            _reject_transcript_fields(item)


def _atomic_write_json(path: Path, payload: Mapping[str, object]) -> None:
    parent = path.parent
    if not parent.is_dir():
        raise CLIError("evaluation_output_unavailable", EXIT_INVALID_INPUT)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_name = handle.name
            json.dump(payload, handle, ensure_ascii=True, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except OSError:
        if temporary_name is not None:
            with suppress(OSError):
                os.unlink(temporary_name)
        raise CLIError("evaluation_output_unavailable", EXIT_DEPENDENCY_FAILURE) from None


def _latest_version(session: Session, source_id: UUID) -> SourceVersion | None:
    return session.scalar(
        select(SourceVersion)
        .where(SourceVersion.source_id == source_id)
        .order_by(SourceVersion.fetched_at.desc(), SourceVersion.id.desc())
        .limit(1)
    )


def _latest_qualification(session: Session, version_id: UUID) -> Qualification | None:
    return session.scalar(
        select(Qualification)
        .where(Qualification.version_id == version_id)
        .order_by(Qualification.checked_at.desc(), Qualification.id.desc())
        .limit(1)
    )


def _qualification_reason_codes(qualification: Qualification) -> tuple[str, ...]:
    reasons: list[str] = []
    for name in sorted(qualification.check_results):
        check = qualification.check_results[name]
        if not isinstance(check, dict):
            continue
        values = check.get("reason_codes")
        if isinstance(values, list):
            reasons.extend(item for item in values if isinstance(item, str))
    return tuple(dict.fromkeys(reasons))


def _source_payload(
    *,
    status: str,
    source_id: UUID,
    reason_codes: Sequence[str],
    version_id: UUID | None = None,
    qualification_id: UUID | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "status": status,
        "source_id": str(source_id),
        "reason_codes": list(reason_codes),
    }
    if version_id is not None:
        payload["version_id"] = str(version_id)
    if qualification_id is not None:
        payload["qualification_id"] = str(qualification_id)
    return payload


def _bounded_reason(value: str) -> str:
    if not isinstance(value, str) or _REASON_CODE_RE.fullmatch(value.strip()) is None:
        raise CLIError("reason_code_invalid", EXIT_INVALID_INPUT)
    return value.strip()


def _parse_uuid(value: object) -> UUID:
    if not isinstance(value, str):
        raise CLIError("source_id_invalid", EXIT_INVALID_INPUT)
    try:
        return UUID(value)
    except ValueError:
        raise CLIError("source_id_invalid", EXIT_INVALID_INPUT) from None


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return left.absolute() == right.absolute()


def _build_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    sources = commands.add_parser("sources")
    source_commands = sources.add_subparsers(dest="source_command", required=True)

    import_command = source_commands.add_parser("import")
    import_command.add_argument("--manifest", required=True)
    for name in ("fetch", "inspect", "qualify", "restore"):
        command = source_commands.add_parser(name)
        command.add_argument("--source-id", required=True)
    withdraw = source_commands.add_parser("withdraw")
    withdraw.add_argument("--source-id", required=True)
    withdraw.add_argument("--reason", required=True)
    source_commands.add_parser("refresh-due")

    evaluate = commands.add_parser("evaluate")
    evaluate.add_argument("--cases", required=True)
    evaluate.add_argument("--output", required=True)
    return parser


def _build_source_operator(settings: Settings) -> SourceOperator:
    engine = create_engine(
        settings.database_url(DatabaseRole.GOVERNANCE),
        connect_args={"connect_timeout": 3, "options": "-c statement_timeout=15000"},
        echo=False,
        hide_parameters=True,
        pool_pre_ping=True,
    )
    return SourceOperator(engine, settings=settings)


def _dispatch_source(namespace: argparse.Namespace, operator: SourceCommands) -> CommandResult:
    command = cast(str, namespace.source_command)
    if command == "import":
        return operator.import_manifest(Path(cast(str, namespace.manifest)))
    if command == "refresh-due":
        return operator.refresh_due()
    source_id = _parse_uuid(namespace.source_id)
    if command == "inspect":
        return operator.inspect(source_id)
    if command == "fetch":
        return operator.fetch(source_id)
    if command == "qualify":
        return operator.qualify(source_id)
    if command == "withdraw":
        return operator.withdraw(source_id, _bounded_reason(namespace.reason))
    if command == "restore":
        return operator.restore(source_id)
    raise CLIError("invalid_arguments", EXIT_INVALID_INPUT)


def _validate_source_arguments(namespace: argparse.Namespace) -> None:
    """Reject malformed operator input before reading credentials or opening a database."""

    if namespace.source_command in ("import", "refresh-due"):
        return
    _parse_uuid(namespace.source_id)
    if namespace.source_command == "withdraw":
        _bounded_reason(namespace.reason)


def _is_authorization_failure(error: DBAPIError) -> bool:
    original = error.orig
    sqlstate = getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)
    return isinstance(sqlstate, str) and (sqlstate.startswith("28") or sqlstate == "42501")


def _emit(stream: IO[str], payload: Mapping[str, object]) -> None:
    stream.write(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    stream.write("\n")
    stream.flush()


def main(
    argv: Sequence[str] | None = None,
    *,
    source_operator: SourceCommands | None = None,
    evaluation_commands: EvaluationCommands | None = None,
    stdout: IO[str] | None = None,
) -> int:
    """Parse, execute, and emit exactly one sanitized JSON command result."""

    output = stdout or sys.stdout
    owned_operator: SourceOperator | None = None
    try:
        namespace = _build_parser().parse_args(argv)
        if namespace.command == "sources":
            _validate_source_arguments(namespace)
            if source_operator is None:
                settings = Settings()
                owned_operator = _build_source_operator(settings)
                source_operator = owned_operator
            result = _dispatch_source(namespace, source_operator)
        elif namespace.command == "evaluate":
            if evaluation_commands is None:
                evaluation_commands = EvaluationService(Settings())
            result = evaluation_commands.evaluate(
                Path(cast(str, namespace.cases)),
                Path(cast(str, namespace.output)),
            )
        else:
            raise CLIError("invalid_arguments", EXIT_INVALID_INPUT)
    except CLIError as error:
        result = CommandResult(
            {"status": "error", "reason_codes": [error.reason_code]},
            error.exit_code,
        )
    except IngestionPipelineError as error:
        invalid_reasons = {
            PipelineFailureReason.MANIFEST_INVALID,
            PipelineFailureReason.MANIFEST_UNAVAILABLE,
        }
        precondition_reasons = {
            PipelineFailureReason.SOURCE_NOT_FOUND,
            PipelineFailureReason.SOURCE_CONFIGURATION_MISSING,
            PipelineFailureReason.DOCUMENT_NOT_DISCOVERED,
        }
        exit_code = (
            EXIT_INVALID_INPUT
            if error.reason in invalid_reasons
            else EXIT_PRECONDITION
            if error.reason in precondition_reasons
            else EXIT_DEPENDENCY_FAILURE
        )
        result = CommandResult(
            {"status": "error", "reason_codes": [error.reason.value]},
            exit_code,
        )
    except SecretFileError:
        result = CommandResult(
            {"status": "error", "reason_codes": ["operator_unauthorized"]},
            EXIT_UNAUTHORIZED,
        )
    except DBAPIError as error:
        reason = (
            "operator_unauthorized" if _is_authorization_failure(error) else "dependency_failure"
        )
        result = CommandResult(
            {"status": "error", "reason_codes": [reason]},
            EXIT_UNAUTHORIZED if _is_authorization_failure(error) else EXIT_DEPENDENCY_FAILURE,
        )
    except (QualificationError, RefreshInputError):
        result = CommandResult(
            {"status": "error", "reason_codes": ["eligibility_precondition_failed"]},
            EXIT_PRECONDITION,
        )
    except (RefreshError, SQLAlchemyError):
        result = CommandResult(
            {"status": "error", "reason_codes": ["dependency_failure"]},
            EXIT_DEPENDENCY_FAILURE,
        )
    except (ValidationError, ValueError, TypeError):
        result = CommandResult(
            {"status": "error", "reason_codes": ["invalid_input"]},
            EXIT_INVALID_INPUT,
        )
    except Exception:
        result = CommandResult(
            {"status": "error", "reason_codes": ["dependency_failure"]},
            EXIT_DEPENDENCY_FAILURE,
        )
    finally:
        if owned_operator is not None:
            owned_operator.close()
    _emit(output, result.payload)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "EXIT_DEPENDENCY_FAILURE",
    "EXIT_INVALID_INPUT",
    "EXIT_PRECONDITION",
    "EXIT_SUCCESS",
    "EXIT_UNAUTHORIZED",
    "CommandResult",
    "EvaluationCase",
    "EvaluationObservation",
    "EvaluationService",
    "SourceOperator",
    "main",
]
