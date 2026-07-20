import asyncio
import contextlib
import uuid
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.cases.durations import materialization
from tracecat.cases.durations.consumer import CaseDurationSyncConsumer
from tracecat.cases.durations.materialization import (
    _event_types_require_sync,
    _get_service_role,
    sync_case_duration,
)
from tracecat.cases.enums import CaseEventType
from tracecat.redis.client import RedisClient, StreamGroupNotFoundError


class FakeRedisClient:
    def __init__(self) -> None:
        self.acked: list[list[str]] = []
        self.deleted: list[list[str]] = []
        self.calls: list[tuple[str, list[str]]] = []

    async def xack(
        self,
        stream_key: str,
        group_name: str,
        message_ids: list[str],
    ) -> None:
        del stream_key, group_name
        self.acked.append(message_ids)
        self.calls.append(("xack", message_ids))

    async def xdel(self, stream_key: str, message_ids: list[str]) -> None:
        del stream_key
        self.deleted.append(message_ids)
        self.calls.append(("xdel", message_ids))


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
    assert client.deleted == [["1-0", "2-0"]]
    assert client.calls == [
        ("xack", ["1-0", "2-0"]),
        ("xdel", ["1-0", "2-0"]),
    ]


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
    assert client.deleted == [["1-0", "2-0"]]


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
                    "event_type": "case_updated",
                },
            )
        ]
    )

    sync_mock.assert_awaited_once()
    assert client.acked == []
    assert client.deleted == []


@pytest.mark.anyio
async def test_shared_sync_case_duration_uses_transaction_scoped_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    case_id = uuid.uuid4()
    fake_session = MagicMock()
    fake_session.commit = AsyncMock()
    fake_session.rollback = AsyncMock()
    role = MagicMock()
    duration_service = MagicMock()
    duration_service.sync_case_durations = AsyncMock(return_value=[])
    duration_service_cls = MagicMock(return_value=duration_service)
    lock_mock = AsyncMock(return_value=True)

    @contextlib.asynccontextmanager
    async def fake_session_context():
        yield fake_session

    monkeypatch.setattr(
        "tracecat.cases.durations.materialization.get_async_session_bypass_rls_context_manager",
        fake_session_context,
    )
    monkeypatch.setattr(
        "tracecat.cases.durations.materialization.try_pg_advisory_xact_lock",
        lock_mock,
    )
    monkeypatch.setattr(
        "tracecat.cases.durations.materialization.CaseDurationService",
        duration_service_cls,
    )
    monkeypatch.setattr(
        "tracecat.cases.durations.materialization._get_service_role",
        AsyncMock(return_value=role),
    )
    monkeypatch.setattr(
        "tracecat.cases.durations.materialization._event_types_require_sync",
        AsyncMock(return_value=True),
    )

    assert await sync_case_duration(workspace_id, case_id)

    lock_mock.assert_awaited_once()
    duration_service_cls.assert_called_once_with(session=fake_session, role=role)
    duration_service.sync_case_durations.assert_awaited_once_with(case_id)
    fake_session.commit.assert_awaited_once()
    fake_session.rollback.assert_not_awaited()


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
                    "event_type": "case_updated",
                },
            )
        ]
    )

    sync_mock.assert_awaited_once()
    logger_mock.assert_called_once()
    assert client.acked == []
    assert client.deleted == []


@pytest.mark.anyio
async def test_consumer_acks_malformed_jobs() -> None:
    client = FakeRedisClient()
    consumer = CaseDurationSyncConsumer(cast(RedisClient, client))

    await consumer._handle_entries([("1-0", {"reason": "case_event"})])

    assert client.acked == [["1-0"]]
    assert client.deleted == [["1-0"]]


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
    assert client.deleted == [["1-0"]]


@pytest.mark.anyio
async def test_consumer_ignores_xdel_failure_after_ack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = AsyncMock()
    client.xdel = AsyncMock(side_effect=RuntimeError("redis cleanup failed"))
    consumer = CaseDurationSyncConsumer(cast(RedisClient, client))
    workspace_id = uuid.uuid4()
    case_id = uuid.uuid4()
    monkeypatch.setattr(
        consumer,
        "_sync_case_duration",
        AsyncMock(return_value=True),
    )
    logger_mock = MagicMock()
    monkeypatch.setattr(
        "tracecat.cases.durations.consumer.logger.warning",
        logger_mock,
    )

    await consumer._handle_entries(
        [
            (
                "1-0",
                {
                    "workspace_id": str(workspace_id),
                    "case_id": str(case_id),
                    "event_type": "case_updated",
                    "reason": "case_event",
                },
            )
        ]
    )

    client.xack.assert_awaited_once_with(
        consumer.stream_key,
        consumer.group,
        ["1-0"],
    )
    client.xdel.assert_awaited_once_with(consumer.stream_key, ["1-0"])
    logger_mock.assert_called_once()


@pytest.mark.parametrize(
    "fields",
    [
        {"reason": "case_event", "event_type": "case_updated"},
        {"reason": "case_event", "case_id": "case-id"},
        {"reason": "case_event", "case_id": "case-id", "cursor": "1"},
        {"reason": "duration_definition_created", "case_id": "case-id"},
        {
            "reason": "duration_definition_created",
            "event_type": "case_updated",
        },
        {"reason": "duration_definition_updated", "case_id": "case-id"},
        {"reason": "duration_definition_updated", "cursor": "1"},
        {"reason": "duration_definition_backfill"},
        {
            "reason": "duration_definition_backfill",
            "case_id": "case-id",
            "cursor": "1",
        },
        {
            "reason": "duration_definition_backfill",
            "case_id": "case-id",
            "event_type": "case_updated",
        },
    ],
)
def test_parse_job_rejects_invalid_reason_shapes(
    fields: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    case_id = uuid.uuid4()
    logger_mock = MagicMock()
    consumer = CaseDurationSyncConsumer(cast(RedisClient, AsyncMock()))
    parsed_fields = {
        "workspace_id": str(workspace_id),
        **{
            key: str(case_id) if value == "case-id" else value
            for key, value in fields.items()
        },
    }
    monkeypatch.setattr(
        "tracecat.cases.durations.consumer.logger.warning",
        logger_mock,
    )

    assert consumer._parse_job(parsed_fields) is None
    logger_mock.assert_called_once()


@pytest.mark.parametrize(
    ("fields", "expected_case_id", "expected_cursor"),
    [
        (
            {
                "reason": "case_event",
                "case_id": "case-id",
                "event_type": "case_updated",
            },
            "case-id",
            None,
        ),
        ({"reason": "duration_definition_created"}, None, None),
        ({"reason": "duration_definition_updated"}, None, None),
        (
            {"reason": "duration_definition_backfill", "case_id": "case-id"},
            "case-id",
            None,
        ),
        ({"reason": "duration_definition_backfill", "cursor": "42"}, None, 42),
    ],
)
def test_parse_job_accepts_published_reason_shapes(
    fields: dict[str, str],
    expected_case_id: str | None,
    expected_cursor: int | None,
) -> None:
    workspace_id = uuid.uuid4()
    case_id = uuid.uuid4()
    consumer = CaseDurationSyncConsumer(cast(RedisClient, AsyncMock()))
    parsed_fields = {
        "workspace_id": str(workspace_id),
        **{
            key: str(case_id) if value == "case-id" else value
            for key, value in fields.items()
        },
    }

    job = consumer._parse_job(parsed_fields)

    assert job is not None
    assert job.workspace_id == workspace_id
    assert getattr(job, "case_id", None) == (
        case_id if expected_case_id is not None else None
    )
    assert getattr(job, "cursor", None) == expected_cursor


@pytest.mark.anyio
async def test_service_role_cache_reuses_workspace_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    workspace = MagicMock(organization_id=organization_id)
    result = MagicMock()
    result.scalars.return_value.first.return_value = workspace
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    monkeypatch.setattr(materialization, "_workspace_role_cache", {})
    monkeypatch.setattr(materialization, "monotonic", MagicMock(side_effect=[0.0, 1.0]))

    first_role = await _get_service_role(cast(AsyncSession, session), workspace_id)
    second_role = await _get_service_role(cast(AsyncSession, session), workspace_id)

    assert first_role is not None
    assert first_role.workspace_id == workspace_id
    assert first_role.organization_id == organization_id
    assert first_role.service_id == "tracecat-case-duration-sync"
    assert second_role is first_role
    session.execute.assert_awaited_once()


@pytest.mark.anyio
async def test_service_role_cache_expiry_detects_deleted_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    workspace = MagicMock(organization_id=uuid.uuid4())
    existing_result = MagicMock()
    existing_result.scalars.return_value.first.return_value = workspace
    deleted_result = MagicMock()
    deleted_result.scalars.return_value.first.return_value = None
    session = MagicMock()
    session.execute = AsyncMock(side_effect=[existing_result, deleted_result])
    logger_mock = MagicMock()
    monkeypatch.setattr(materialization, "_workspace_role_cache", {})
    monkeypatch.setattr(
        materialization,
        "monotonic",
        MagicMock(side_effect=[0.0, materialization._WORKSPACE_ROLE_CACHE_TTL_SECONDS]),
    )
    monkeypatch.setattr(materialization.logger, "info", logger_mock)

    assert (
        await _get_service_role(cast(AsyncSession, session), workspace_id) is not None
    )
    assert await _get_service_role(cast(AsyncSession, session), workspace_id) is None

    assert session.execute.await_count == 2
    logger_mock.assert_called_once()


@pytest.mark.anyio
async def test_ensure_group_reads_backlog_from_start() -> None:
    client = AsyncMock()
    consumer = CaseDurationSyncConsumer(cast(RedisClient, client))

    await consumer._ensure_group()

    # "0" ensures jobs published during the startup gap (before the group
    # exists) are still delivered, rather than being skipped by "$".
    client.xgroup_create.assert_awaited_once_with(
        consumer.stream_key,
        consumer.group,
        id="0",
        ignore_busygroup=True,
    )


@pytest.mark.anyio
async def test_event_types_require_sync_matches_status_changed_aliases() -> None:
    session = cast(AsyncSession, FakeDefinitionMatchSession())
    workspace_id = uuid.uuid4()

    assert await _event_types_require_sync(
        session,
        workspace_id=workspace_id,
        event_types={"case_closed"},
    )
    assert await _event_types_require_sync(
        session,
        workspace_id=workspace_id,
        event_types={"case_reopened"},
    )
    assert not await _event_types_require_sync(
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


@pytest.mark.anyio
async def test_consumer_retries_transient_read_failure_and_processes_next_batch(
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
            ConnectionError("redis unavailable"),
            [("stream", entries)],
            asyncio.CancelledError(),
        ]
    )
    consumer = CaseDurationSyncConsumer(cast(RedisClient, client))
    ensure_group_mock = AsyncMock()
    handle_entries_mock = AsyncMock()
    sleep_mock = AsyncMock()
    monkeypatch.setattr(
        "tracecat.cases.durations.consumer.config.TRACECAT__CASE_DURATION_SYNC_ENABLED",
        True,
    )
    monkeypatch.setattr(consumer, "_ensure_group", ensure_group_mock)
    monkeypatch.setattr(consumer, "_handle_entries", handle_entries_mock)
    monkeypatch.setattr("tracecat.cases.durations.consumer.asyncio.sleep", sleep_mock)

    with pytest.raises(asyncio.CancelledError):
        await consumer.run()

    ensure_group_mock.assert_awaited_once()
    handle_entries_mock.assert_awaited_once_with(entries)
    assert [call.args[0] for call in sleep_mock.await_args_list] == [1.0, 0]


@pytest.mark.anyio
async def test_consumer_recreates_missing_group_and_keeps_reading(
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
            StreamGroupNotFoundError("NOGROUP No such consumer group"),
            [("stream", entries)],
            asyncio.CancelledError(),
        ]
    )
    consumer = CaseDurationSyncConsumer(cast(RedisClient, client))
    ensure_group_mock = AsyncMock()
    handle_entries_mock = AsyncMock()
    sleep_mock = AsyncMock()
    monkeypatch.setattr(
        "tracecat.cases.durations.consumer.config.TRACECAT__CASE_DURATION_SYNC_ENABLED",
        True,
    )
    monkeypatch.setattr(consumer, "_ensure_group", ensure_group_mock)
    monkeypatch.setattr(consumer, "_handle_entries", handle_entries_mock)
    monkeypatch.setattr("tracecat.cases.durations.consumer.asyncio.sleep", sleep_mock)

    with pytest.raises(asyncio.CancelledError):
        await consumer.run()

    # Initial creation plus one recreation, with no backoff sleep in between.
    assert ensure_group_mock.await_count == 2
    handle_entries_mock.assert_awaited_once_with(entries)
    assert [call.args[0] for call in sleep_mock.await_args_list] == [0]


@pytest.mark.anyio
async def test_consumer_retries_transient_group_creation_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = AsyncMock()
    client.xreadgroup = AsyncMock(side_effect=asyncio.CancelledError())
    consumer = CaseDurationSyncConsumer(cast(RedisClient, client))
    ensure_group_mock = AsyncMock(
        side_effect=[ConnectionError("redis unavailable"), None]
    )
    sleep_mock = AsyncMock()
    monkeypatch.setattr(
        "tracecat.cases.durations.consumer.config.TRACECAT__CASE_DURATION_SYNC_ENABLED",
        True,
    )
    monkeypatch.setattr(consumer, "_ensure_group", ensure_group_mock)
    monkeypatch.setattr("tracecat.cases.durations.consumer.asyncio.sleep", sleep_mock)

    with pytest.raises(asyncio.CancelledError):
        await consumer.run()

    assert ensure_group_mock.await_count == 2
    assert [call.args[0] for call in sleep_mock.await_args_list] == [1.0]


@pytest.mark.anyio
async def test_consumer_resets_backoff_after_successful_iteration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = AsyncMock()
    client.xreadgroup = AsyncMock(
        side_effect=[
            ConnectionError("first outage"),
            [],
            ConnectionError("second outage"),
            asyncio.CancelledError(),
        ]
    )
    consumer = CaseDurationSyncConsumer(cast(RedisClient, client))
    sleep_mock = AsyncMock()
    monkeypatch.setattr(
        "tracecat.cases.durations.consumer.config.TRACECAT__CASE_DURATION_SYNC_ENABLED",
        True,
    )
    monkeypatch.setattr(consumer, "_ensure_group", AsyncMock())
    monkeypatch.setattr("tracecat.cases.durations.consumer.asyncio.sleep", sleep_mock)

    with pytest.raises(asyncio.CancelledError):
        await consumer.run()

    assert [call.args[0] for call in sleep_mock.await_args_list] == [1.0, 0, 1.0]
