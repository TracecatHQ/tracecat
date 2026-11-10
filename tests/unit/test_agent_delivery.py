"""Tests for the channel delivery contract, port resolution, and in-activity delivery."""

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
    NoopChannelDeliveryPort,
    TurnFinished,
    approval_batch_id,
    deliver_notices,
    get_channel_delivery_port,
    linked_thread_hint,
)
from tracecat.agent.session.activities import FinalizeTurnInput
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
        self.attempts = 0

    async def has_linked_thread(self, role: Role, session_id: uuid.UUID) -> bool:
        del role, session_id
        if self.error is not None:
            raise self.error
        return True

    async def deliver(self, role: Role, notice: ChannelNotice) -> str | None:
        del role
        self.attempts += 1
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
    request = FinalizeTurnInput(
        role=ROLE, session_id=SESSION_ID, run_id=RUN_ID, thread_notices=[notice]
    )
    hint: Any = FinalizeTurnInput
    [decoded] = converter.from_payloads(converter.to_payloads([request]), [hint])
    assert decoded == request
    assert type(decoded.thread_notices[0]) is type(notice)


def test_approval_batch_id_ignores_tool_call_order() -> None:
    first = approval_batch_id(RUN_ID, ["call-b", "call-a"])
    assert first == approval_batch_id(RUN_ID, ["call-a", "call-b"])
    assert first != approval_batch_id(RUN_ID, ["call-a"])
    assert first != approval_batch_id(uuid.uuid4(), ["call-a", "call-b"])


@pytest.fixture
def no_backoff(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record retry delays instead of sleeping them."""
    delays: list[float] = []

    async def sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(delivery.asyncio, "sleep", sleep)
    return delays


@pytest.mark.anyio
async def test_noop_port_is_the_default(installed) -> None:
    installed()
    port = get_channel_delivery_port()
    assert isinstance(port, NoopChannelDeliveryPort)
    assert await port.has_linked_thread(ROLE, SESSION_ID) is False
    assert await deliver_notices(ROLE, [TURN_FINISHED]) == [None]


@pytest.mark.anyio
async def test_installed_port_receives_the_notices_in_order(installed) -> None:
    port = _RecordingPort()
    installed(_EntryPoint("example", lambda: port))

    refs = await deliver_notices(ROLE, NOTICES[:2])

    assert refs == ["ts-1", "ts-1"]
    assert port.notices == NOTICES[:2]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("error", "attempts", "delays"),
    [
        (RuntimeError("down"), 3, [2, 4]),
        (ChannelDeliveryRetryAfter(timedelta(seconds=7)), 3, [7, 7]),
        (ApplicationError("gone", non_retryable=True), 1, []),
    ],
    ids=["transient", "retry-after", "definitive"],
)
async def test_a_failed_delivery_retries_then_gives_up_quietly(
    installed,
    no_backoff: list[float],
    error: Exception,
    attempts: int,
    delays: list[float],
) -> None:
    port = _RecordingPort(error=error)
    installed(_EntryPoint("example", lambda: port))

    # The next notice still goes out after one gives up.
    assert await deliver_notices(ROLE, [TURN_FINISHED, TURN_FINISHED]) == [None, None]

    assert port.attempts == 2 * attempts
    assert no_backoff == delays * 2


@pytest.mark.anyio
async def test_a_broken_install_never_fails_the_host(installed) -> None:
    installed(_EntryPoint("a", _RecordingPort), _EntryPoint("b", _RecordingPort))
    with pytest.raises(RuntimeError, match="a, b"):
        get_channel_delivery_port()

    assert await deliver_notices(ROLE, [TURN_FINISHED]) == [None]


class _HangingPort(_RecordingPort):
    async def has_linked_thread(self, role: Role, session_id: uuid.UUID) -> bool:
        await asyncio.Event().wait()
        return True

    async def deliver(self, role: Role, notice: ChannelNotice) -> str | None:
        await asyncio.Event().wait()
        return "ts-1"


@pytest.mark.anyio
async def test_a_hanging_provider_is_cut_off_at_the_budget(
    installed, monkeypatch: pytest.MonkeyPatch
) -> None:
    installed(_EntryPoint("example", _HangingPort))
    monkeypatch.setattr(delivery, "THREAD_DELIVERY_BUDGET", timedelta(milliseconds=50))

    async with asyncio.timeout(1):
        refs = await deliver_notices(ROLE, [TURN_FINISHED, TURN_FINISHED])

    assert refs == [None, None]


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
