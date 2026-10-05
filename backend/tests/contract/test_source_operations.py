"""Contract coverage for bounded source-governance CLI operations."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256
from io import StringIO
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from app.cli import (
    EXIT_DEPENDENCY_FAILURE,
    EXIT_INVALID_INPUT,
    EXIT_PRECONDITION,
    CommandResult,
    main,
)
from app.config import MAX_DOCUMENT_BYTES
from app.ingestion.discovery import (
    DiscoveredDocument,
    DiscoveryInputError,
    DiscoveryPolicy,
)
from app.ingestion.extract import StructuredExtractor
from app.ingestion.versions import SourceVersionService
from app.models.enums import ExtractionStatus, MediaType, SourceStatus
from app.models.sources import Source, SourceVersion
from pytest import MonkeyPatch
from sqlalchemy.orm import Session

pytestmark = pytest.mark.contract

SOURCE_ID = UUID("10000000-0000-4000-8000-000000000062")
VERSION_ID = UUID("20000000-0000-4000-8000-000000000062")
QUALIFICATION_ID = UUID("30000000-0000-4000-8000-000000000062")
OBSERVED_AT = datetime(2030, 1, 16, 13, tzinfo=UTC)
SOURCE_URL = "https://www.pnw.edu/__test__/contract/t062/"


class _LifecycleCommands:
    """Stateful test double for the CLI's typed source-operator boundary."""

    def __init__(self) -> None:
        self.status = "eligible"
        self.calls: list[tuple[object, ...]] = []

    def import_manifest(self, manifest_path: Path) -> CommandResult:
        self.calls.append(("import", manifest_path))
        return CommandResult(
            {
                "status": "success",
                "source_ids": [str(SOURCE_ID)],
                "created_source_ids": [str(SOURCE_ID)],
                "reason_codes": [],
            }
        )

    def fetch(self, source_id: UUID) -> CommandResult:
        self.calls.append(("fetch", source_id))
        return CommandResult(self._payload(source_id))

    def inspect(self, source_id: UUID) -> CommandResult:
        self.calls.append(("inspect", source_id))
        return CommandResult(self._payload(source_id))

    def qualify(self, source_id: UUID) -> CommandResult:
        self.calls.append(("qualify", source_id))
        self.status = "quarantined"
        return CommandResult(
            self._payload(source_id, reasons=("evidence_incomplete",)),
            EXIT_PRECONDITION,
        )

    def withdraw(self, source_id: UUID, reason_code: str) -> CommandResult:
        self.calls.append(("withdraw", source_id, reason_code))
        self.status = "withdrawn"
        return CommandResult(self._payload(source_id, reasons=(reason_code,)))

    def restore(self, source_id: UUID) -> CommandResult:
        self.calls.append(("restore", source_id))
        self.status = "stale"
        return CommandResult(
            self._payload(
                source_id,
                reasons=("restoration_requires_requalification",),
            )
        )

    def refresh_due(self) -> CommandResult:
        self.calls.append(("refresh-due",))
        self.status = "stale"
        return CommandResult(
            {
                "status": "incomplete",
                "reason_codes": ["fetch_timeout"],
                "source_ids": [str(SOURCE_ID)],
                "refreshed_source_ids": [],
                "failed_source_ids": [str(SOURCE_ID)],
                "expired_source_ids": [],
            },
            EXIT_DEPENDENCY_FAILURE,
        )

    def _payload(
        self,
        source_id: UUID,
        *,
        reasons: tuple[str, ...] = (),
    ) -> dict[str, object]:
        return {
            "status": self.status,
            "source_id": str(source_id),
            "version_id": str(VERSION_ID),
            "qualification_id": str(QUALIFICATION_ID),
            "reason_codes": list(reasons),
        }


def _run(
    commands: _LifecycleCommands,
    *arguments: str,
) -> tuple[int, dict[str, object]]:
    output = StringIO()
    exit_code = main(
        ("sources", *arguments),
        source_operator=commands,
        stdout=output,
    )
    return exit_code, json.loads(output.getvalue())


def test_source_bounds_are_hard_upper_limits() -> None:
    accepted = DiscoveryPolicy(
        max_crawl_depth=3,
        max_urls_per_run=100,
        max_document_bytes=MAX_DOCUMENT_BYTES,
    )

    assert accepted.max_crawl_depth == 3
    assert accepted.max_urls_per_run == 100
    assert accepted.max_document_bytes == 20 * 1024 * 1024

    with pytest.raises(DiscoveryInputError):
        DiscoveryPolicy(max_crawl_depth=4)
    with pytest.raises(DiscoveryInputError):
        DiscoveryPolicy(max_urls_per_run=101)
    with pytest.raises(DiscoveryInputError):
        DiscoveryPolicy(max_document_bytes=MAX_DOCUMENT_BYTES + 1)


def test_manifest_import_and_fetch_are_exposed_by_the_cli_contract(tmp_path: Path) -> None:
    commands = _LifecycleCommands()
    manifest = tmp_path / "sources.json"
    manifest.write_text('{"sources": []}', encoding="utf-8")

    import_exit, imported = _run(commands, "import", "--manifest", str(manifest))
    fetch_exit, fetched = _run(commands, "fetch", "--source-id", str(SOURCE_ID))

    assert import_exit == fetch_exit == 0
    assert imported["source_ids"] == [str(SOURCE_ID)]
    assert imported["created_source_ids"] == [str(SOURCE_ID)]
    assert fetched["status"] == "eligible"
    assert commands.calls == [("import", manifest), ("fetch", SOURCE_ID)]


def test_invalid_identifiers_and_reason_codes_fail_before_operator_dispatch() -> None:
    commands = _LifecycleCommands()

    invalid_id_exit, invalid_id = _run(commands, "inspect", "--source-id", "not-a-uuid")
    invalid_reason_exit, invalid_reason = _run(
        commands,
        "withdraw",
        "--source-id",
        str(SOURCE_ID),
        "--reason",
        "free-form reason",
    )
    oversized_reason_exit, oversized_reason = _run(
        commands,
        "withdraw",
        "--source-id",
        str(SOURCE_ID),
        "--reason",
        "a" * 129,
    )

    assert invalid_id_exit == invalid_reason_exit == oversized_reason_exit == EXIT_INVALID_INPUT
    assert invalid_id == {"status": "error", "reason_codes": ["source_id_invalid"]}
    assert invalid_reason == {"status": "error", "reason_codes": ["reason_code_invalid"]}
    assert oversized_reason == invalid_reason
    assert commands.calls == []


def test_qualification_and_failed_refresh_preserve_bounded_reason_codes() -> None:
    commands = _LifecycleCommands()

    qualify_exit, qualification = _run(commands, "qualify", "--source-id", str(SOURCE_ID))
    refresh_exit, refresh = _run(commands, "refresh-due")

    assert qualify_exit == EXIT_PRECONDITION
    assert qualification["status"] == "quarantined"
    assert qualification["reason_codes"] == ["evidence_incomplete"]
    assert refresh_exit == EXIT_DEPENDENCY_FAILURE
    assert refresh == {
        "status": "incomplete",
        "reason_codes": ["fetch_timeout"],
        "source_ids": [str(SOURCE_ID)],
        "refreshed_source_ids": [],
        "failed_source_ids": [str(SOURCE_ID)],
        "expired_source_ids": [],
    }


def test_withdrawal_and_restore_require_requalification() -> None:
    commands = _LifecycleCommands()

    withdraw_exit, withdrawn = _run(
        commands,
        "withdraw",
        "--source-id",
        str(SOURCE_ID),
        "--reason",
        "policy_removed",
    )
    restore_exit, restored = _run(commands, "restore", "--source-id", str(SOURCE_ID))

    assert withdraw_exit == restore_exit == 0
    assert withdrawn["status"] == "withdrawn"
    assert withdrawn["reason_codes"] == ["policy_removed"]
    assert restored["status"] == "stale"
    assert restored["reason_codes"] == ["restoration_requires_requalification"]
    assert commands.calls == [
        ("withdraw", SOURCE_ID, "policy_removed"),
        ("restore", SOURCE_ID),
    ]


def test_repeated_identical_content_import_reuses_the_immutable_version(
    monkeypatch: MonkeyPatch,
) -> None:
    content = b"<main><h1>Synthetic policy</h1><p>Contract evidence.</p></main>"
    document = DiscoveredDocument(
        requested_url=SOURCE_URL,
        canonical_url=SOURCE_URL,
        title="Synthetic T062 policy",
        topics=("synthetic_contract",),
        media_type=MediaType.HTML,
        content=content,
        depth=0,
        parent_url=None,
        redirect_chain=(),
    )
    extraction = StructuredExtractor().extract(document)
    source = Source(
        id=SOURCE_ID,
        canonical_url=SOURCE_URL,
        title=document.title,
        media_type=MediaType.HTML,
        status=SourceStatus.CANDIDATE,
    )
    existing = SourceVersion(
        id=uuid4(),
        source_id=source.id,
        content_sha256=sha256(content).hexdigest(),
        content=content.decode(),
        fetched_at=OBSERVED_AT,
        extraction_status=ExtractionStatus.COMPLETE,
        parser_version=extraction.parser_version,
    )
    session = Session()
    added: list[object] = []
    monkeypatch.setattr(session, "scalar", lambda _statement: existing)
    monkeypatch.setattr(session, "add", lambda instance, _warn=True: added.append(instance))
    monkeypatch.setattr(session, "flush", lambda _objects=None: None)

    result = SourceVersionService(
        session,
        clock=lambda: OBSERVED_AT,
    ).record(source=source, document=document, extraction=extraction)

    assert result.created is False
    assert result.version is existing
    assert result.version.fetched_at == OBSERVED_AT
    assert not any(isinstance(item, SourceVersion) for item in added)
