from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CorpusBuild, CorpusBuildStatus


class CorpusServiceError(RuntimeError):
    """Base exception for corpus lifecycle failures."""


class CorpusBuildNotFoundError(CorpusServiceError):
    pass


class CorpusBuildTransitionError(CorpusServiceError):
    pass


_ALLOWED_TRANSITIONS: dict[CorpusBuildStatus, frozenset[CorpusBuildStatus]] = {
    CorpusBuildStatus.BUILDING: frozenset(
        {CorpusBuildStatus.VALIDATED, CorpusBuildStatus.FAILED}
    ),
    CorpusBuildStatus.VALIDATED: frozenset({CorpusBuildStatus.FAILED}),
    CorpusBuildStatus.PROMOTED: frozenset({CorpusBuildStatus.RETIRED}),
    CorpusBuildStatus.FAILED: frozenset(),
    CorpusBuildStatus.RETIRED: frozenset(),
}


def _completed_at_for(status: CorpusBuildStatus) -> datetime | None:
    if status in {
        CorpusBuildStatus.VALIDATED,
        CorpusBuildStatus.PROMOTED,
        CorpusBuildStatus.FAILED,
        CorpusBuildStatus.RETIRED,
    }:
        return datetime.now(timezone.utc)
    return None


class CorpusService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[None]:
        if self.session.in_transaction():
            yield
            return

        async with self.session.begin():
            yield

    async def get_active_corpus(self) -> CorpusBuild | None:
        async with self._transaction():
            result = await self.session.execute(
                select(CorpusBuild)
                .where(CorpusBuild.status == CorpusBuildStatus.PROMOTED)
                .order_by(CorpusBuild.started_at.desc())
            )
            return result.scalar_one_or_none()

    async def require_active_corpus(self) -> CorpusBuild:
        corpus = await self.get_active_corpus()
        if corpus is None:
            raise CorpusServiceError("no promoted corpus build is available")
        return corpus

    async def transition(
        self,
        version: str,
        target_status: CorpusBuildStatus,
    ) -> CorpusBuild:
        async with self._transaction():
            build = await self._get_locked_build(version)
            allowed_statuses = _ALLOWED_TRANSITIONS[build.status]
            if target_status not in allowed_statuses:
                raise CorpusBuildTransitionError(
                    f"cannot transition corpus {version!r} from "
                    f"{build.status.value} to {target_status.value}"
                )

            build.status = target_status
            completed_at = _completed_at_for(target_status)
            if completed_at is not None:
                build.completed_at = completed_at
            await self.session.flush()
            return build

    async def promote(self, version: str) -> CorpusBuild:
        async with self._transaction():
            candidate = await self._get_locked_build(version)
            if candidate.status is not CorpusBuildStatus.VALIDATED:
                raise CorpusBuildTransitionError(
                    f"only validated corpus builds can be promoted; {version!r} is "
                    f"{candidate.status.value}"
                )

            active_result = await self.session.execute(
                select(CorpusBuild)
                .where(CorpusBuild.status == CorpusBuildStatus.PROMOTED)
                .with_for_update()
            )
            active_build = active_result.scalar_one_or_none()
            if active_build is not None and active_build.id == candidate.id:
                return candidate

            if active_build is not None:
                active_build.status = CorpusBuildStatus.RETIRED
                active_build.completed_at = datetime.now(timezone.utc)
                await self.session.flush()

            candidate.status = CorpusBuildStatus.PROMOTED
            candidate.completed_at = datetime.now(timezone.utc)
            await self.session.flush()
            return candidate

    async def rollback_to(self, version: str) -> CorpusBuild:
        async with self._transaction():
            target = await self._get_locked_build(version)
            if target.status not in {
                CorpusBuildStatus.RETIRED,
                CorpusBuildStatus.VALIDATED,
            }:
                raise CorpusBuildTransitionError(
                    f"only retired or validated corpus builds can be restored; "
                    f"{version!r} is {target.status.value}"
                )

            active_result = await self.session.execute(
                select(CorpusBuild)
                .where(CorpusBuild.status == CorpusBuildStatus.PROMOTED)
                .with_for_update()
            )
            active_build = active_result.scalar_one_or_none()
            if active_build is not None and active_build.id != target.id:
                active_build.status = CorpusBuildStatus.RETIRED
                active_build.completed_at = datetime.now(timezone.utc)
                await self.session.flush()

            target.status = CorpusBuildStatus.PROMOTED
            target.completed_at = datetime.now(timezone.utc)
            await self.session.flush()
            return target

    async def _get_locked_build(self, version: str) -> CorpusBuild:
        result = await self.session.execute(
            select(CorpusBuild)
            .where(CorpusBuild.version == version)
            .with_for_update()
        )
        build = result.scalar_one_or_none()
        if build is None:
            raise CorpusBuildNotFoundError(f"corpus build {version!r} was not found")
        return build


async def get_active_corpus(session: AsyncSession) -> CorpusBuild | None:
    return await CorpusService(session).get_active_corpus()


async def promote_corpus(session: AsyncSession, version: str) -> CorpusBuild:
    return await CorpusService(session).promote(version)


async def rollback_corpus(session: AsyncSession, version: str) -> CorpusBuild:
    return await CorpusService(session).rollback_to(version)