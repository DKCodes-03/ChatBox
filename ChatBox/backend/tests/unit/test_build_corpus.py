from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.models import CorpusBuild, CorpusBuildStatus
from ingestion.build_corpus import validate_build_requirements


def test_validate_build_requirements_rejects_incomplete_construction() -> None:
    build = CorpusBuild(
        id=uuid4(),
        version="candidate-1",
        embedding_model="test-model",
        embedding_dimensions=3,
        source_count=0,
        chunk_count=0,
        status=CorpusBuildStatus.BUILDING,
        started_at=datetime.now(timezone.utc),
    )

    with pytest.raises(ValueError, match="at least one source"):
        validate_build_requirements(build)
