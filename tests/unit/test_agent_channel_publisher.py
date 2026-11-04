"""Tests for run updates, publisher resolution, and in-activity publishing."""

import asyncio
import uuid
from datetime import timedelta
from typing import Any

import pytest
from temporalio.exceptions import ApplicationError

from tracecat.agent import channel_publisher
from tracecat.agent.channel_publisher import (
    ApprovalDecided,
    ApprovalPending,
    ChannelPublishRetryAfter,
    RunUpdate,
    TurnFinished,
    approval_batch_id,
    get_channel_publisher,
    is_channel_linked,
    publish_updates,
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
UPDATES: list[RunUpdate] = [
    TURN_FINISHED,
    ApprovalPending(session_id=SESSION_ID, run_id=RUN_ID, batch_id=BATCH_ID),
    ApprovalDecided(session_id=SESSION_ID, batch_id=BATCH_ID, pending_ref="ref-1"),
    ApprovalDecided(session_id=SESSION_ID, batch_id=BATCH_ID, pending_ref=None),
]


class _EntryPoint:
    def __init__(self, name: str, target: object) -> None:
        self.name = name
        self._target = target

    def load(self) -> object:
        return self._target


class _RecordingPublisher:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.updates: list[RunUpdate] = []
        self.attempts = 0

    async def is_linked(self, role: Role, session_id: uuid.UUID) -> bool:
        del role, session_id
        if self.error is not None:
            raise self.error
        return True

    async def publish(self, role: Role, update: RunUpdate) -> str | None:
        del role
        self.attempts += 1
        if self.error is not None:
            raise self.error
        self.updates.append(update)
        return "ref-1"


@pytest.fixture
def installed(monkeypatch: pytest.MonkeyPatch):
    """Install entry points for the publisher group, then reset the cache."""

    def install(*entries: _EntryPoint) -> None:
        def fake_entry_points(*, group: str) -> list[_EntryPoint]:
            assert group == channel_publisher.CHANNEL_PUBLISHER_ENTRY_POINT_GROUP
            return list(entries)

        monkeypatch.setattr(channel_publisher, "entry_points", fake_entry_points)
        get_channel_publisher.cache_clear()

    yield install
    get_channel_publisher.cache_clear()


@pytest.mark.parametrize(
    "update", UPDATES, ids=[f"{type(n).__name__}-{i}" for i, n in enumerate(UPDATES)]
)
def test_update_round_trip_decodes_with_alias_hint(update: RunUpdate) -> None:
    converter = PydanticPayloadConverter()
    request = FinalizeTurnInput(
        role=ROLE, session_id=SESSION_ID, run_id=RUN_ID, channel_updates=[update]
    )
    hint: Any = FinalizeTurnInput
    [decoded] = converter.from_payloads(converter.to_payloads([request]), [hint])
    assert decoded == request
    assert type(decoded.channel_updates[0]) is type(update)


def test_approval_batch_id_ignores_tool_call_order() -> None:
    first = approval_batch_id(RUN_ID, ["call-b", "call-a"])
    assert first == approval_batch_id(RUN_ID, ["call-a", "call-b"])
    # Approval rows are unique per tool call id.
    assert first == approval_batch_id(RUN_ID, ["call-a", "call-b", "call-a"])
    assert first != approval_batch_id(RUN_ID, ["call-a"])
    assert first != approval_batch_id(uuid.uuid4(), ["call-a", "call-b"])


@pytest.fixture
def no_backoff(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record retry delays instead of sleeping them."""
    delays: list[float] = []

    async def sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(channel_publisher.asyncio, "sleep", sleep)
    return delays


@pytest.mark.anyio
async def test_without_a_publisher_no_session_is_linked(installed) -> None:
    installed()
    assert get_channel_publisher() is None
    assert await is_channel_linked(ROLE, SESSION_ID) is False
    # Unreachable from a run, but still never fails the host.
    assert await publish_updates(ROLE, [TURN_FINISHED]) == [None]


@pytest.mark.anyio
async def test_installed_publisher_receives_the_updates_in_order(installed) -> None:
    publisher = _RecordingPublisher()
    installed(_EntryPoint("example", lambda: publisher))

    refs = await publish_updates(ROLE, UPDATES[:2])

    assert refs == ["ref-1", "ref-1"]
    assert publisher.updates == UPDATES[:2]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("error", "attempts", "delays"),
    [
        (RuntimeError("down"), 3, [2, 4]),
        (ChannelPublishRetryAfter(timedelta(seconds=7)), 3, [7, 7]),
        (ApplicationError("gone", non_retryable=True), 1, []),
    ],
    ids=["transient", "retry-after", "definitive"],
)
async def test_a_failed_publish_retries_then_gives_up_quietly(
    installed,
    no_backoff: list[float],
    error: Exception,
    attempts: int,
    delays: list[float],
) -> None:
    publisher = _RecordingPublisher(error=error)
    installed(_EntryPoint("example", lambda: publisher))

    # The next update still goes out after one gives up.
    assert await publish_updates(ROLE, [TURN_FINISHED, TURN_FINISHED]) == [None, None]

    assert publisher.attempts == 2 * attempts
    assert no_backoff == delays * 2


@pytest.mark.anyio
async def test_a_broken_install_never_fails_the_host(installed) -> None:
    installed(
        _EntryPoint("a", _RecordingPublisher), _EntryPoint("b", _RecordingPublisher)
    )
    with pytest.raises(RuntimeError, match="a, b"):
        get_channel_publisher()

    assert await publish_updates(ROLE, [TURN_FINISHED]) == [None]


class _HangingPublisher(_RecordingPublisher):
    async def is_linked(self, role: Role, session_id: uuid.UUID) -> bool:
        await asyncio.Event().wait()
        return True

    async def publish(self, role: Role, update: RunUpdate) -> str | None:
        await asyncio.Event().wait()
        return "ref-1"


@pytest.mark.anyio
async def test_a_hanging_provider_is_cut_off_at_the_budget(
    installed, monkeypatch: pytest.MonkeyPatch
) -> None:
    installed(_EntryPoint("example", _HangingPublisher))
    monkeypatch.setattr(channel_publisher, "PUBLISH_BUDGET", timedelta(milliseconds=50))

    async with asyncio.timeout(1):
        refs = await publish_updates(ROLE, [TURN_FINISHED, TURN_FINISHED])

    assert refs == [None, None]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("publisher", "expected"),
    [
        (_RecordingPublisher(), True),
        (_RecordingPublisher(error=RuntimeError("down")), False),
        (_HangingPublisher(), False),
    ],
    ids=["linked", "publisher-error", "publisher-timeout"],
)
async def test_is_channel_linked(
    installed,
    monkeypatch: pytest.MonkeyPatch,
    publisher: object,
    expected: bool,
) -> None:
    installed(_EntryPoint("example", lambda: publisher))
    monkeypatch.setattr(channel_publisher, "CHANNEL_LINK_CHECK_TIMEOUT_SECONDS", 0.01)

    linked = await is_channel_linked(ROLE, SESSION_ID)

    assert linked is expected
