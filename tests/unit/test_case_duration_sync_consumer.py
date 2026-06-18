import asyncio
import uuid
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from tracecat.cases.durations.consumer import CaseDurationSyncConsumer
from tracecat.cases.enums import CaseEventType
from tracecat.redis.client import RedisClient


class FakeRedisClient:
    def __init__(self) -> None:
        self.acked: list[list[str]] = []

    async def xack(
        self,
        stream_key: str,
        group_name: str,
        message_ids: list[str],
    ) -> None:
        del stream_key, group_name
        self.acked.append(message_ids)


class FakeScalarResult:
    def __init__(self, value: uuid.UUID | None) -> None:
        self.value = value

    def scalar_one_or_none(self) -> uuid.UUID | None:
        return self.value


class FakeDefinitionMatchSession:
    async def execute(self, stmt: Any) -> FakeScalarResult:
        compiled = stmt.compile()
        event_types = {
            event_type
            for value in compiled.params.values()
            if isinstance(value, list)
            for event_type in value
        }
        return FakeScalarResult(
            uuid.uuid4() if CaseEventType.STATUS_CHANGED in event_types else None
        )


@pytest.mark.anyio
async def test_consumer_coalesces_case_jobs_by_case(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeRedisClient()
    consumer = CaseDurationSyncConsumer(cast(RedisClient, client))
    workspace_id = uuid.uuid4()
    case_id = uuid.uuid4()
    sync_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(consumer, "_sync_case_duration", sync_mock)

    await consumer._handle_entries(
        [
            (
                "1-0",
                {
                    "workspace_id": str(workspace_id),
                    "case_id": str(case_id),
                    "reason": "case_event",
                    "event_type": "case_updated",
                },
            ),
            (
                "2-0",
                {
                    "workspace_id": str(workspace_id),
                    "case_id": str(case_id),
                    "reason": "case_event",
                    "event_type": "case_updated",
                },
            ),
        ]
    )

    sync_mock.assert_awaited_once_with(
        workspace_id,
        case_id,
        event_types={"case_updated"},
    )
    assert client.acked == [["1-0", "2-0"]]


@pytest.mark.anyio
async def test_consumer_forces_sync_when_backfill_coalesces_with_case_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeRedisClient()
    consumer = CaseDurationSyncConsumer(cast(RedisClient, client))
    workspace_id = uuid.uuid4()
    case_id = uuid.uuid4()
    sync_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(consumer, "_sync_case_duration", sync_mock)

    # A per-case backfill job (no event_type) coalesced with a non-matching
    # case_event must still force an unconditional sync, not be filtered out.
    await consumer._handle_entries(
        [
            (
                "1-0",
                {
                    "workspace_id": str(workspace_id),
                    "case_id": str(case_id),
                    "reason": "duration_definition_backfill",
                },
            ),
            (
                "2-0",
                {
                    "workspace_id": str(workspace_id),
                    "case_id": str(case_id),
                    "reason": "case_event",
                    "event_type": "case_updated",
                },
            ),
        ]
    )

    sync_mock.assert_awaited_once_with(
        workspace_id,
        case_id,
        event_types=None,
    )
    assert client.acked == [["1-0", "2-0"]]


@pytest.mark.anyio
async def test_consumer_leaves_locked_case_jobs_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeRedisClient()
    consumer = CaseDurationSyncConsumer(cast(RedisClient, client))
    sync_mock = AsyncMock(return_value=False)
    monkeypatch.setattr(consumer, "_sync_case_duration", sync_mock)

    await consumer._handle_entries(
        [
            (
                "1-0",
                {
                    "workspace_id": str(uuid.uuid4()),
                    "case_id": str(uuid.uuid4()),
                    "reason": "case_event",
                },
            )
        ]
    )

    sync_mock.assert_awaited_once()
    assert client.acked == []


@pytest.mark.anyio
async def test_consumer_leaves_failed_case_jobs_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeRedisClient()
    consumer = CaseDurationSyncConsumer(cast(RedisClient, client))
    sync_mock = AsyncMock(side_effect=RuntimeError("transient db failure"))
    logger_mock = MagicMock()
    monkeypatch.setattr(consumer, "_sync_case_duration", sync_mock)
    monkeypatch.setattr(
        "tracecat.cases.durations.consumer.logger.exception",
        logger_mock,
    )

    await consumer._handle_entries(
        [
            (
                "1-0",
                {
                    "workspace_id": str(uuid.uuid4()),
                    "case_id": str(uuid.uuid4()),
                    "reason": "case_event",
                },
            )
        ]
    )

    sync_mock.assert_awaited_once()
    logger_mock.assert_called_once()
    assert client.acked == []


@pytest.mark.anyio
async def test_consumer_acks_malformed_jobs() -> None:
    client = FakeRedisClient()
    consumer = CaseDurationSyncConsumer(cast(RedisClient, client))

    await consumer._handle_entries([("1-0", {"reason": "case_event"})])

    assert client.acked == [["1-0"]]


@pytest.mark.anyio
async def test_consumer_acks_successful_backfill_jobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeRedisClient()
    consumer = CaseDurationSyncConsumer(cast(RedisClient, client))
    workspace_id = uuid.uuid4()
    backfill_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(consumer, "_process_backfill_job", backfill_mock)

    await consumer._handle_entries(
        [
            (
                "1-0",
                {
                    "workspace_id": str(workspace_id),
                    "reason": "duration_definition_created",
                },
            )
        ]
    )

    backfill_mock.assert_awaited_once()
    await_args = backfill_mock.await_args
    assert await_args is not None
    job = await_args.args[0]
    assert cast(Any, job).workspace_id == workspace_id
    assert client.acked == [["1-0"]]


@pytest.mark.anyio
async def test_event_types_require_sync_matches_status_changed_aliases() -> None:
    session = FakeDefinitionMatchSession()
    consumer = CaseDurationSyncConsumer(cast(RedisClient, FakeRedisClient()))
    workspace_id = uuid.uuid4()

    assert await consumer._event_types_require_sync(
        session,
        workspace_id=workspace_id,
        event_types={"case_closed"},
    )
    assert await consumer._event_types_require_sync(
        session,
        workspace_id=workspace_id,
        event_types={"case_reopened"},
    )
    assert not await consumer._event_types_require_sync(
        session,
        workspace_id=workspace_id,
        event_types={"case_updated"},
    )


@pytest.mark.anyio
async def test_consumer_claims_idle_messages_while_stream_is_busy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entries = [
        (
            "1-0",
            {
                "workspace_id": str(uuid.uuid4()),
                "case_id": str(uuid.uuid4()),
                "reason": "case_event",
            },
        )
    ]
    client = AsyncMock()
    client.xreadgroup = AsyncMock(
        side_effect=[
            [("stream", entries)],
            asyncio.CancelledError(),
        ]
    )
    consumer = CaseDurationSyncConsumer(cast(RedisClient, client))
    consumer._pending_check_interval = 10
    ensure_group_mock = AsyncMock()
    handle_entries_mock = AsyncMock()
    claim_idle_mock = AsyncMock()
    monotonic_mock = MagicMock(side_effect=[0.0, 11.0])
    monkeypatch.setattr(
        "tracecat.cases.durations.consumer.config.TRACECAT__CASE_DURATION_SYNC_ENABLED",
        True,
    )
    monkeypatch.setattr(
        "tracecat.cases.durations.consumer.monotonic",
        monotonic_mock,
    )
    monkeypatch.setattr(consumer, "_ensure_group", ensure_group_mock)
    monkeypatch.setattr(consumer, "_handle_entries", handle_entries_mock)
    monkeypatch.setattr(consumer, "_claim_idle_messages", claim_idle_mock)

    with pytest.raises(asyncio.CancelledError):
        await consumer.run()

    ensure_group_mock.assert_awaited_once()
    handle_entries_mock.assert_awaited_once_with(entries)
    claim_idle_mock.assert_awaited_once()
