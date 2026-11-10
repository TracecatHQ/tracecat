"""Tests for the channel delivery contract, port resolution, and activity."""

import asyncio
import uuid
from datetime import timedelta
from typing import Any

import pytest
from temporalio.exceptions import ApplicationError

from tracecat.agent import delivery
from tracecat.agent.delivery import (
    ApprovalDecided,
    ApprovalPending,
    ChannelDeliveryRetryAfter,
    ChannelNotice,
    ChannelNoticeResult,
    DeliverChannelNoticeInput,
    NoopChannelDeliveryPort,
    TurnFinished,
    approval_batch_id,
    deliver_thread_notice,
    get_channel_delivery_port,
    linked_thread_hint,
)
from tracecat.auth.types import Role
from tracecat.dsl._converter import PydanticPayloadConverter

SESSION_ID = uuid.UUID("00000000-0000-4000-8000-00000000005e")
RUN_ID = uuid.UUID("00000000-0000-4000-8000-0000000000a1")
BATCH_ID = uuid.UUID("00000000-0000-4000-8000-0000000000ba")
ROLE = Role(
    type="service",
    service_id="tracecat-agent-executor",
    workspace_id=uuid.UUID("00000000-0000-4000-8000-0000000000c0"),
)

TURN_FINISHED = TurnFinished(session_id=SESSION_ID, run_id=RUN_ID)
NOTICES: list[ChannelNotice] = [
    TURN_FINISHED,
    ApprovalPending(session_id=SESSION_ID, run_id=RUN_ID, batch_id=BATCH_ID),
    ApprovalDecided(session_id=SESSION_ID, batch_id=BATCH_ID, previous_ref="ts-1"),
    ApprovalDecided(session_id=SESSION_ID, batch_id=BATCH_ID, previous_ref=None),
]


class _EntryPoint:
    def __init__(self, name: str, target: object) -> None:
        self.name = name
        self._target = target

    def load(self) -> object:
        return self._target


class _RecordingPort:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.notices: list[ChannelNotice] = []

    async def has_linked_thread(self, role: Role, session_id: uuid.UUID) -> bool:
        del role, session_id
        if self.error is not None:
            raise self.error
        return True

    async def deliver(self, role: Role, notice: ChannelNotice) -> str | None:
        del role
        if self.error is not None:
            raise self.error
        self.notices.append(notice)
        return "ts-1"


@pytest.fixture
def installed(monkeypatch: pytest.MonkeyPatch):
    """Install entry points for the delivery group, then reset the cache."""

    def install(*entries: _EntryPoint) -> None:
        def fake_entry_points(*, group: str) -> list[_EntryPoint]:
            assert group == delivery.CHANNEL_DELIVERY_ENTRY_POINT_GROUP
            return list(entries)

        monkeypatch.setattr(delivery, "entry_points", fake_entry_points)
        get_channel_delivery_port.cache_clear()

    yield install
    get_channel_delivery_port.cache_clear()


@pytest.mark.parametrize(
    "notice", NOTICES, ids=[f"{type(n).__name__}-{i}" for i, n in enumerate(NOTICES)]
)
def test_notice_round_trip_decodes_with_alias_hint(notice: ChannelNotice) -> None:
    converter = PydanticPayloadConverter()
    request = DeliverChannelNoticeInput(role=ROLE, notice=notice)
    hint: Any = DeliverChannelNoticeInput
    [decoded] = converter.from_payloads(converter.to_payloads([request]), [hint])
    assert decoded == request
    assert type(decoded.notice) is type(notice)


def test_approval_batch_id_ignores_tool_call_order() -> None:
    first = approval_batch_id(RUN_ID, ["call-b", "call-a"])
    assert first == approval_batch_id(RUN_ID, ["call-a", "call-b"])
    assert first != approval_batch_id(RUN_ID, ["call-a"])
    assert first != approval_batch_id(uuid.uuid4(), ["call-a", "call-b"])


@pytest.mark.anyio
async def test_noop_port_is_the_default(installed) -> None:
    installed()
    port = get_channel_delivery_port()
    assert isinstance(port, NoopChannelDeliveryPort)
    assert await port.has_linked_thread(ROLE, SESSION_ID) is False

    result = await deliver_thread_notice(
        DeliverChannelNoticeInput(role=ROLE, notice=TURN_FINISHED)
    )
    assert result == ChannelNoticeResult(ref=None)


@pytest.mark.anyio
async def test_installed_port_receives_the_notice(installed) -> None:
    port = _RecordingPort()
    installed(_EntryPoint("example", lambda: port))

    result = await deliver_thread_notice(
        DeliverChannelNoticeInput(role=ROLE, notice=TURN_FINISHED)
    )

    assert result == ChannelNoticeResult(ref="ts-1")
    assert port.notices == [TURN_FINISHED]


@pytest.mark.anyio
async def test_retry_after_becomes_the_next_retry_delay(installed) -> None:
    port = _RecordingPort(error=ChannelDeliveryRetryAfter(timedelta(seconds=7)))
    installed(_EntryPoint("example", lambda: port))

    with pytest.raises(ApplicationError) as exc_info:
        await deliver_thread_notice(
            DeliverChannelNoticeInput(role=ROLE, notice=TURN_FINISHED)
        )

    assert exc_info.value.next_retry_delay == timedelta(seconds=7)
    assert exc_info.value.non_retryable is False


@pytest.mark.anyio
async def test_multiple_ports_fail_delivery_without_retries(installed) -> None:
    installed(_EntryPoint("a", _RecordingPort), _EntryPoint("b", _RecordingPort))
    with pytest.raises(RuntimeError, match="a, b"):
        get_channel_delivery_port()

    with pytest.raises(ApplicationError) as exc_info:
        await deliver_thread_notice(
            DeliverChannelNoticeInput(role=ROLE, notice=TURN_FINISHED)
        )

    assert exc_info.value.non_retryable is True


class _HangingPort(_RecordingPort):
    async def has_linked_thread(self, role: Role, session_id: uuid.UUID) -> bool:
        await asyncio.Event().wait()
        return True


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("port", "expected"),
    [
        (NoopChannelDeliveryPort(), False),
        (_RecordingPort(), True),
        (_RecordingPort(error=RuntimeError("down")), False),
        (_HangingPort(), False),
    ],
    ids=["noop", "linked", "port-error", "port-timeout"],
)
async def test_linked_thread_hint(
    installed,
    monkeypatch: pytest.MonkeyPatch,
    port: object,
    expected: bool,
) -> None:
    installed(_EntryPoint("example", lambda: port))
    monkeypatch.setattr(delivery, "LINKED_THREAD_HINT_TIMEOUT_SECONDS", 0.01)

    linked = await linked_thread_hint(ROLE, SESSION_ID)

    assert linked is expected
