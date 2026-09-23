from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.models import CorpusBuild, CorpusBuildStatus
from app.services.corpus_service import (
    CorpusBuildTransitionError,
    CorpusService,
)


class Result:
    def __init__(self, value: CorpusBuild | None) -> None:
        self.value = value

    def scalar_one_or_none(self) -> CorpusBuild | None:
        return self.value


class Transaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_: object) -> None:
        return None


class FakeSession:
    def __init__(self, results: list[Result]) -> None:
        self.results = results
        self.begin_count = 0
        self.flush_count = 0

    def in_transaction(self) -> None:
        return None

    def begin(self) -> Transaction:
        self.begin_count += 1
        return Transaction()

    async def execute(self, _statement: object) -> Result:
        return self.results.pop(0)

    async def flush(self) -> None:
        self.flush_count += 1


def build(version: str, status: CorpusBuildStatus) -> CorpusBuild:
    return CorpusBuild(
        id=uuid4(),
        version=version,
        embedding_model="test-model",
        embedding_dimensions=3,
        status=status,
        started_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_promote_requires_validated_build() -> None:
    candidate = build("candidate", CorpusBuildStatus.BUILDING)
    service = CorpusService(FakeSession([Result(candidate)]))  # type: ignore[arg-type]

    with pytest.raises(CorpusBuildTransitionError, match="only validated"):
        await service.promote("candidate")


@pytest.mark.asyncio
async def test_promotion_retires_previous_active_build() -> None:
    candidate = build("candidate", CorpusBuildStatus.VALIDATED)
    active = build("active", CorpusBuildStatus.PROMOTED)
    session = FakeSession([Result(candidate), Result(active)])
    service = CorpusService(session)  # type: ignore[arg-type]

    promoted = await service.promote("candidate")

    assert promoted is candidate
    assert candidate.status is CorpusBuildStatus.PROMOTED
    assert active.status is CorpusBuildStatus.RETIRED
    assert session.flush_count == 2


@pytest.mark.asyncio
async def test_active_lookup_then_promotion_can_share_session() -> None:
    active = build("active", CorpusBuildStatus.PROMOTED)
    candidate = build("candidate", CorpusBuildStatus.VALIDATED)
    session = FakeSession([Result(active), Result(candidate), Result(active)])
    service = CorpusService(session)  # type: ignore[arg-type]

    assert await service.get_active_corpus() is active
    await service.promote("candidate")

    assert session.begin_count == 2