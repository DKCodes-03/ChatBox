from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.models import CorpusBuild, CorpusBuildStatus


@dataclass(frozen=True, slots=True)
class BuildValidationResult:
    ok: bool
    reason: str | None = None


def validate_build_requirements(build: CorpusBuild) -> None:
    """Ensure a corpus build is complete before it can be promoted.

    The build must have at least one source and one chunk, a valid embedding model
    configuration, and a status that indicates it is ready for validation.
    """
    if build.source_count <= 0:
        raise ValueError("corpus build must include at least one source before validation")
    if build.chunk_count <= 0:
        raise ValueError("corpus build must include at least one chunk before validation")
    if build.embedding_model.strip() == "":
        raise ValueError("embedding model name must not be empty")
    if build.embedding_dimensions <= 0:
        raise ValueError("embedding dimensions must be positive")
    if build.status not in {CorpusBuildStatus.VALIDATED, CorpusBuildStatus.BUILDING}:
        raise ValueError("corpus build must be in BUILDING or VALIDATED state before promotion")


def mark_build_failed(build: CorpusBuild, *, reason: str) -> CorpusBuild:
    """Mark a build as failed and ensure it cannot be promoted."""
    build.status = CorpusBuildStatus.FAILED
    build.validation_summary = reason
    build.completed_at = datetime.now(timezone.utc)
    return build


def promote_if_valid(build: CorpusBuild) -> CorpusBuild:
    """Promote a build only if all required checks pass."""
    validate_build_requirements(build)
    build.status = CorpusBuildStatus.VALIDATED
    build.completed_at = datetime.now(timezone.utc)
    return build


def rollback_build(build: CorpusBuild) -> CorpusBuild:
    """Reset a failed or incomplete build to a safe terminal state."""
    if build.status in {CorpusBuildStatus.BUILDING, CorpusBuildStatus.VALIDATED}:
        build.status = CorpusBuildStatus.FAILED
    build.completed_at = datetime.now(timezone.utc)
    return build


def main() -> None:
    raise SystemExit("Use the application runtime to execute corpus builds and promotions.")


if __name__ == "__main__":
    main()
