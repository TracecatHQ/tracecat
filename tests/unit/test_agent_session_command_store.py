"""Postgres-backed tests for ``PgSessionCommandStore``.

Every store call commits its own transaction, so these tests use their own
committed fixtures instead of the savepoint-scoped ``session`` fixture.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest
from pydantic import JsonValue
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from tests.conftest import TEST_ORG_ID
from tests.database import TEST_DB_CONFIG
from tracecat.agent.control.enums import CommandState, CommandType
from tracecat.agent.control.payloads import (
    AbortPayload,
    CommandPayload,
    DecidePayload,
    SteerPayload,
    SubmitPayload,
)
from tracecat.agent.control.store import (
    PgSessionCommandStore,
    command_channel,
    payload_hash,
)
from tracecat.agent.control.types import (
    RootSessionNotFoundError,
    SessionCommand,
    SessionCommandConflictError,
    SessionCommandTypeMismatchError,
)
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.db.models import AgentSession, AgentSessionCommand, Workspace

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("db")]


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(TEST_DB_CONFIG.test_url, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
def factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
def store(factory: async_sessionmaker[AsyncSession]) -> PgSessionCommandStore:
    return PgSessionCommandStore(factory)


@pytest.fixture
async def root(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AgentSession]:
    """A committed root session in its own workspace, deleted afterwards."""
    workspace = Workspace(
        id=uuid.uuid4(), name="command-store-test", organization_id=TEST_ORG_ID
    )
    agent_session = AgentSession(
        id=uuid.uuid4(),
        workspace_id=workspace.id,
        title="Test",
        entity_type=AgentSessionEntity.WORKSPACE_CHAT.value,
        entity_id=workspace.id,
    )
    async with factory() as session:
        session.add(workspace)
        await session.flush()
        session.add(agent_session)
        await session.commit()
    try:
        yield agent_session
    finally:
        async with factory() as session:
            # Cascades to the session and its commands.
            await session.execute(delete(Workspace).where(Workspace.id == workspace.id))
            await session.commit()


def _approve(override_args: dict[str, JsonValue]) -> DecidePayload:
    return DecidePayload(action="approve", override_args=override_args)


def _command(
    root: AgentSession,
    request_id: str,
    payload: CommandPayload | None = None,
    *,
    type: CommandType | None = None,
    state: CommandState = CommandState.ACCEPTED,
    generation: uuid.UUID | None = None,
) -> SessionCommand:
    assert state in (CommandState.QUEUED, CommandState.ACCEPTED)
    payload = payload if payload is not None else SteerPayload(text=request_id)
    return SessionCommand(
        root_session_id=root.id,
        session_id=root.id,
        request_id=request_id,
        type=type if type is not None else payload.type,
        payload=payload,
        state=state,
        turn_id=None if state == CommandState.QUEUED else uuid.uuid4(),
        generation=generation,
    )


async def test_concurrent_admits_get_distinct_increasing_seqs(
    store: PgSessionCommandStore, root: AgentSession
) -> None:
    """Admission serializes per root: concurrent admits get seq 1..n, no gaps."""
    admissions = await asyncio.gather(
        *(store.admit(_command(root, f"req-{i}")) for i in range(16))
    )

    assert sorted(admission.row.seq for admission in admissions) == list(range(1, 17))
    assert not any(admission.existing for admission in admissions)
    assert {admission.row.workspace_id for admission in admissions} == {
        root.workspace_id
    }
    pending = await store.pending(root.id)
    assert [row.seq for row in pending] == list(range(1, 17))
    by_seq = {admission.row.seq: admission.row.request_id for admission in admissions}
    assert [row.request_id for row in pending] == [by_seq[row.seq] for row in pending]


async def test_seq_is_scoped_per_root(
    store: PgSessionCommandStore,
    root: AgentSession,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """Each root counts its own seq from 1."""
    other = AgentSession(
        id=uuid.uuid4(),
        workspace_id=root.workspace_id,
        title="Other",
        entity_type=AgentSessionEntity.WORKSPACE_CHAT.value,
        entity_id=root.workspace_id,
    )
    async with factory() as session:
        session.add(other)
        await session.commit()

    first = await store.admit(_command(root, "same-request"))
    second = await store.admit(_command(other, "same-request"))

    assert (first.row.seq, second.row.seq) == (1, 1)
    assert first.row.id != second.row.id


async def test_readmit_with_same_payload_returns_the_existing_row(
    store: PgSessionCommandStore,
    root: AgentSession,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """A retried request ID returns its first row, even from another turn."""
    first = await store.admit(_command(root, "req", _approve({"a": 1, "b": 2})))
    # Same payload with keys reordered and a different turn: still a retry.
    retried = await store.admit(_command(root, "req", _approve({"b": 2, "a": 1})))
    concurrent = await asyncio.gather(
        *(
            store.admit(_command(root, "req", _approve({"a": 1, "b": 2})))
            for _ in range(4)
        )
    )

    assert not first.existing
    assert retried.existing
    assert retried.row == first.row
    assert all(admission.existing for admission in concurrent)
    assert {admission.row.id for admission in concurrent} == {first.row.id}
    async with factory() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(AgentSessionCommand)
            .where(AgentSessionCommand.root_session_id == root.id)
        )
    assert count == 1


async def test_concurrent_first_admits_of_one_request_create_one_row(
    store: PgSessionCommandStore, root: AgentSession
) -> None:
    """Racing first admits of one request ID: exactly one creates the row."""
    admissions = await asyncio.gather(
        *(store.admit(_command(root, "req", SteerPayload(text="hi"))) for _ in range(8))
    )

    assert sum(not admission.existing for admission in admissions) == 1
    assert {admission.row.id for admission in admissions} == {admissions[0].row.id}


@pytest.mark.parametrize("change", ["payload", "type", "session"])
async def test_readmit_of_another_command_conflicts(
    store: PgSessionCommandStore, root: AgentSession, change: str
) -> None:
    """A request ID reused for a different command is refused, not overwritten."""
    first = await store.admit(_command(root, "req", SteerPayload(text="hi")))
    retry = _command(root, "req", SteerPayload(text="hi"))
    match change:
        case "payload":
            retry = _command(root, "req", SteerPayload(text="bye"))
        case "type":
            retry = _command(root, "req", SubmitPayload(input="hi"))
        case _:
            retry = replace(retry, session_id=uuid.uuid4())

    with pytest.raises(SessionCommandConflictError):
        await store.admit(retry)

    (row,) = await store.pending(root.id)
    assert row == first.row


async def test_admit_refuses_a_type_that_disagrees_with_the_payload(
    store: PgSessionCommandStore, root: AgentSession
) -> None:
    with pytest.raises(SessionCommandTypeMismatchError):
        await store.admit(
            _command(root, "req", SteerPayload(text="hi"), type=CommandType.SUBMIT)
        )

    assert await store.get(root.id, "req") is None


async def test_rows_read_back_as_typed_payloads(
    store: PgSessionCommandStore, root: AgentSession
) -> None:
    decide = _approve({"path": "/tmp/x"})
    await store.admit(_command(root, "decide", decide))
    await store.admit(_command(root, "abort", AbortPayload()))

    stored = [row.payload for row in await store.pending(root.id)]

    assert stored == [decide, AbortPayload()]


async def test_admit_for_a_missing_root_raises(store: PgSessionCommandStore) -> None:
    missing = AgentSession(id=uuid.uuid4())

    with pytest.raises(RootSessionNotFoundError):
        await store.admit(_command(missing, "req"))


async def test_mark_only_moves_forward(
    store: PgSessionCommandStore, root: AgentSession
) -> None:
    """States never move back, terminal states are final, receipts persist."""
    await store.admit(_command(root, "req", state=CommandState.QUEUED))

    accepted = await store.mark(root.id, "req", CommandState.ACCEPTED)
    applied = await store.mark(root.id, "req", CommandState.APPLIED, {"ok": True})
    # Moving back is refused; the stored row comes back unchanged.
    back = await store.mark(root.id, "req", CommandState.QUEUED)
    # A receipt of None keeps the stored receipt.
    settled = await store.mark(root.id, "req", CommandState.SETTLED)
    after_terminal = await store.mark(root.id, "req", CommandState.WITHDRAWN)

    assert accepted is not None and accepted.state == CommandState.ACCEPTED
    assert applied is not None and applied.state == CommandState.APPLIED
    assert applied.receipt == {"ok": True}
    assert back == applied
    assert settled is not None and settled.state == CommandState.SETTLED
    assert settled.receipt == {"ok": True}
    assert after_terminal == settled


@pytest.mark.parametrize("start", [CommandState.QUEUED, CommandState.ACCEPTED])
async def test_withdraw_takes_a_command_the_backend_has_not_applied(
    store: PgSessionCommandStore, root: AgentSession, start: CommandState
) -> None:
    await store.admit(_command(root, "req", state=start))

    withdrawn = await store.mark(root.id, "req", CommandState.WITHDRAWN)

    assert withdrawn is not None and withdrawn.state == CommandState.WITHDRAWN
    assert await store.pending(root.id) == []


@pytest.mark.parametrize("target", [CommandState.WITHDRAWN, CommandState.REJECTED])
async def test_an_applied_command_cannot_be_withdrawn_or_rejected(
    store: PgSessionCommandStore, root: AgentSession, target: CommandState
) -> None:
    """Too late: an applied command stays applied, and the caller can tell."""
    await store.admit(_command(root, "req"))
    applied = await store.mark(root.id, "req", CommandState.APPLIED)

    refused = await store.mark(root.id, "req", target, {"error": "late"})

    assert refused == applied
    assert refused is not None and refused.state == CommandState.APPLIED


async def test_accepting_a_queued_command_binds_it_to_its_turn_once(
    store: PgSessionCommandStore, root: AgentSession
) -> None:
    """A queued follow-up joins the turn that takes it and never moves to another."""
    turn, generation = uuid.uuid4(), uuid.uuid4()
    await store.admit(_command(root, "follow-up", state=CommandState.QUEUED))

    accepted = await store.mark(
        root.id, "follow-up", CommandState.ACCEPTED, turn_id=turn, generation=generation
    )
    rebound = await store.mark(
        root.id,
        "follow-up",
        CommandState.APPLIED,
        turn_id=uuid.uuid4(),
        generation=uuid.uuid4(),
    )

    assert accepted is not None
    assert (accepted.turn_id, accepted.generation) == (turn, generation)
    assert rebound is not None and rebound.state == CommandState.APPLIED
    assert (rebound.turn_id, rebound.generation) == (turn, generation)
    rows = await store.rows(root.id, generation=generation)
    assert [row.request_id for row in rows] == ["follow-up"]


async def test_mark_to_the_same_state_records_the_receipt(
    store: PgSessionCommandStore, root: AgentSession
) -> None:
    await store.admit(_command(root, "req"))

    marked = await store.mark(root.id, "req", CommandState.ACCEPTED, {"step": 1})

    assert marked is not None
    assert (marked.state, marked.receipt) == (CommandState.ACCEPTED, {"step": 1})


async def test_mark_of_a_missing_command_returns_none(
    store: PgSessionCommandStore, root: AgentSession
) -> None:
    assert await store.mark(root.id, "missing", CommandState.APPLIED) is None


async def test_pending_lists_only_active_commands_in_seq_order(
    store: PgSessionCommandStore, root: AgentSession
) -> None:
    states = [
        CommandState.QUEUED,
        CommandState.ACCEPTED,
        CommandState.APPLIED,
        CommandState.REJECTED,
        CommandState.WITHDRAWN,
        CommandState.SETTLED,
    ]
    for state in states:
        initial = (
            CommandState.QUEUED
            if state == CommandState.QUEUED
            else CommandState.ACCEPTED
        )
        await store.admit(_command(root, state.value, state=initial))
        await store.mark(root.id, state.value, state)

    pending = await store.pending(root.id)

    assert [row.request_id for row in pending] == ["queued", "accepted", "applied"]
    assert [row.seq for row in pending] == [1, 2, 3]


async def test_get_returns_the_command_for_a_request_id(
    store: PgSessionCommandStore, root: AgentSession
) -> None:
    admission = await store.admit(_command(root, "req"))

    assert await store.get(root.id, "req") == admission.row
    assert await store.get(root.id, "missing") is None
    assert await store.get(uuid.uuid4(), "req") is None


async def test_rows_lists_one_generation_in_every_state_in_seq_order(
    store: PgSessionCommandStore, root: AgentSession
) -> None:
    turn, other = uuid.uuid4(), uuid.uuid4()
    await store.admit(
        _command(root, "submit", SubmitPayload(input="go"), generation=turn)
    )
    await store.admit(_command(root, "other", generation=other))
    await store.admit(_command(root, "steer", generation=turn))
    await store.admit(_command(root, "abort", AbortPayload(), generation=turn))
    await store.admit(_command(root, "queued", state=CommandState.QUEUED))
    await store.mark(root.id, "submit", CommandState.SETTLED, {"done": True})
    await store.mark(root.id, "steer", CommandState.REJECTED)

    rows = await store.rows(root.id, generation=turn)

    assert [(row.request_id, row.state) for row in rows] == [
        ("submit", CommandState.SETTLED),
        ("steer", CommandState.REJECTED),
        ("abort", CommandState.ACCEPTED),
    ]
    assert rows[0].receipt == {"done": True}


async def test_retain_deletes_expired_terminal_rows_but_keeps_the_newest(
    store: PgSessionCommandStore, root: AgentSession
) -> None:
    """Retention never deletes the newest row, so seq keeps counting up."""
    for request_id in ("old-1", "old-2", "old-3", "active"):
        await store.admit(_command(root, request_id))
    for request_id in ("old-1", "old-2"):
        await store.mark(root.id, request_id, CommandState.SETTLED)
    await store.mark(root.id, "old-3", CommandState.REJECTED)
    await store.mark(root.id, "active", CommandState.SETTLED)

    # Nothing is old enough yet.
    assert await store.retain(root.id) == 0

    later = datetime.now(UTC) + timedelta(hours=25)
    assert await store.retain(root.id, now=later) == 3

    survivor = await store.mark(root.id, "active", CommandState.SETTLED)
    assert survivor is not None and survivor.seq == 4
    assert await store.mark(root.id, "old-1", CommandState.SETTLED) is None
    readmitted = await store.admit(_command(root, "next"))
    assert readmitted.row.seq == 5


async def test_retain_keeps_active_rows(
    store: PgSessionCommandStore, root: AgentSession
) -> None:
    await store.admit(_command(root, "queued", state=CommandState.QUEUED))
    await store.admit(_command(root, "newest"))

    later = datetime.now(UTC) + timedelta(hours=25)

    assert await store.retain(root.id, now=later) == 0
    assert [row.request_id for row in await store.pending(root.id)] == [
        "queued",
        "newest",
    ]


async def test_retain_keeps_a_generation_that_still_has_an_active_command(
    store: PgSessionCommandStore, root: AgentSession
) -> None:
    """A turn can outlive retention; its settled rows stay until it finishes."""
    running, finished = uuid.uuid4(), uuid.uuid4()
    await store.admit(_command(root, "running-submit", generation=running))
    await store.admit(_command(root, "finished-submit", generation=finished))
    await store.admit(_command(root, "running-steer", generation=running))
    await store.admit(_command(root, "newest"))
    for request_id in ("running-submit", "finished-submit", "newest"):
        await store.mark(root.id, request_id, CommandState.SETTLED)

    later = datetime.now(UTC) + timedelta(hours=25)

    assert await store.retain(root.id, now=later) == 1
    assert await store.get(root.id, "finished-submit") is None
    assert [
        row.request_id for row in await store.rows(root.id, generation=running)
    ] == [
        "running-submit",
        "running-steer",
    ]

    await store.mark(root.id, "running-steer", CommandState.SETTLED)
    assert await store.retain(root.id, now=later) == 2
    assert await store.rows(root.id, generation=running) == []


async def test_admit_stores_the_payload_it_hashed(
    factory: async_sessionmaker[AsyncSession], root: AgentSession
) -> None:
    """A caller mutating a nested value mid-admit cannot split payload from hash."""
    payload = _approve({"path": "original"})
    assert payload.override_args is not None
    override_args = payload.override_args

    def mutating_factory() -> AsyncSession:
        # Admission hashes before it opens a session, and inserts after. A
        # frozen model still holds a mutable dict.
        override_args["path"] = "mutated"
        return factory()

    store = PgSessionCommandStore(mutating_factory)
    admission = await store.admit(_command(root, "req", payload))

    original = _approve({"path": "original"})
    assert admission.row.payload == original
    assert admission.row.payload_hash == payload_hash(original)


class _SlowCommitSession(AsyncSession):
    """Holds each commit back so a test can look before it lands."""

    commit_started: asyncio.Event

    async def commit(self) -> None:
        self.commit_started.set()
        await asyncio.sleep(0.3)
        await super().commit()


async def test_notify_is_delivered_only_after_admission_commits(
    engine: AsyncEngine, root: AgentSession
) -> None:
    """A listener wakes once per new row, and never before the row is visible."""
    notifications: list[tuple[str, str]] = []
    commit_started = asyncio.Event()
    _SlowCommitSession.commit_started = commit_started
    store = PgSessionCommandStore(
        async_sessionmaker(engine, class_=_SlowCommitSession, expire_on_commit=False)
    )
    channel = command_channel(root.id)
    listener = await asyncpg.connect(TEST_DB_CONFIG.test_url.replace("+asyncpg", ""))
    try:
        await listener.add_listener(
            channel,
            lambda _conn, _pid, name, payload: notifications.append((name, payload)),
        )

        admitting = asyncio.create_task(store.admit(_command(root, "req")))
        await commit_started.wait()
        await asyncio.sleep(0.1)
        assert notifications == []

        admission = await admitting
        async with asyncio.timeout(5):
            while not notifications:
                await asyncio.sleep(0.05)
        assert notifications == [(channel, str(admission.row.seq))]

        # A retry admits nothing new and sends no wake-up.
        assert (await store.admit(_command(root, "req"))).existing
        await asyncio.sleep(0.2)
        assert len(notifications) == 1
    finally:
        await listener.close()


def test_command_channel_fits_a_postgres_identifier() -> None:
    root_session_id = uuid.uuid4()

    channel = command_channel(root_session_id)

    assert len(channel) <= 63
    assert channel.endswith(root_session_id.hex)
