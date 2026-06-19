import uuid
from collections.abc import AsyncIterator, Sequence
from typing import cast
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.cases.durations.service import CaseDurationDefinitionService


class FakeScalarStream:
    def __init__(self, batches: list[Sequence[uuid.UUID]]) -> None:
        self.batches = batches
        self.closed = False
        self.partition_sizes: list[int | None] = []

    async def partitions(
        self, size: int | None = None
    ) -> AsyncIterator[Sequence[uuid.UUID]]:
        self.partition_sizes.append(size)
        for batch in self.batches:
            yield batch

    async def close(self) -> None:
        self.closed = True


class FakeInlineBackfillSession:
    def __init__(self, stream: FakeScalarStream) -> None:
        self.stream = stream
        self.flush = AsyncMock()
        self.execute = AsyncMock(side_effect=AssertionError("execute should not run"))
        self.stream_scalars_calls: list[object] = []

    async def stream_scalars(self, stmt: object) -> FakeScalarStream:
        self.stream_scalars_calls.append(stmt)
        return self.stream


@pytest.mark.anyio
async def test_inline_definition_backfill_streams_case_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case_ids = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]
    stream = FakeScalarStream([case_ids[:2], case_ids[2:]])
    session = FakeInlineBackfillSession(stream)
    role = Role(
        type="service",
        service_id="tracecat-api",
        organization_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
    )
    duration_service = MagicMock()
    duration_service.sync_case_durations = AsyncMock(return_value=[])
    duration_service_cls = MagicMock(return_value=duration_service)
    monkeypatch.setattr(
        "tracecat.cases.durations.service.CaseDurationService",
        duration_service_cls,
    )

    definition_service = CaseDurationDefinitionService(
        session=cast(AsyncSession, session),
        role=role,
    )

    await definition_service._sync_existing_case_durations_inline()

    session.flush.assert_awaited_once()
    session.execute.assert_not_called()
    assert len(session.stream_scalars_calls) == 1
    assert stream.partition_sizes == [8]
    assert stream.closed is True
    duration_service_cls.assert_called_once_with(session=session, role=role)
    assert duration_service.sync_case_durations.await_args_list == [
        call(case_id) for case_id in case_ids
    ]
