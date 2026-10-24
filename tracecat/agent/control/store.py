"""Postgres store for the ``agent_session_command`` table.

This module holds the only SQL for the table. Admission serializes per root
session with a transaction-scoped advisory lock, so ``seq`` commits in order,
and sends a ``NOTIFY`` on the root's channel in the same transaction. The
notification is only a wake hint; the table stays the source of truth.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta

from pydantic import JsonValue
from sqlalchemy import delete, exists, func, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from tracecat.agent.control.enums import CommandState, CommandType
from tracecat.agent.control.payloads import COMMAND_PAYLOAD, CommandPayload
from tracecat.agent.control.types import (
    Admission,
    CommandReceipt,
    RootSessionNotFoundError,
    SessionCommand,
    SessionCommandConflictError,
    SessionCommandRow,
    SessionCommandTypeMismatchError,
)
from tracecat.db.models import AgentSession, AgentSessionCommand

type SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]

RETENTION = timedelta(hours=24)

ACTIVE_STATES = (CommandState.QUEUED, CommandState.ACCEPTED, CommandState.APPLIED)
TERMINAL_STATES = (
    CommandState.REJECTED,
    CommandState.WITHDRAWN,
    CommandState.SETTLED,
)

# The states `mark` may move a command out of, per target state. States rank
# queued < accepted < applied < settled, a command never leaves a terminal
# state, and only a command the backend has not applied can be rejected or
# withdrawn.
_FROM_STATES: dict[CommandState, tuple[CommandState, ...]] = {
    CommandState.QUEUED: (CommandState.QUEUED,),
    CommandState.ACCEPTED: (CommandState.QUEUED, CommandState.ACCEPTED),
    CommandState.APPLIED: ACTIVE_STATES,
    CommandState.REJECTED: (CommandState.QUEUED, CommandState.ACCEPTED),
    CommandState.SETTLED: ACTIVE_STATES,
    CommandState.WITHDRAWN: (CommandState.QUEUED, CommandState.ACCEPTED),
}

_CHANNEL_PREFIX = "agent_session_command_"
_LOCK = text(
    "SELECT pg_advisory_xact_lock("
    "hashtextextended('agent_session_command:' || CAST(:root AS text), 0))"
)


def command_channel(root_session_id: uuid.UUID) -> str:
    """The ``LISTEN`` channel that admission notifies for a root session.

    Postgres truncates identifiers past 63 bytes; this name is 54.
    """
    return f"{_CHANNEL_PREFIX}{root_session_id.hex}"


def _canonical(payload: CommandPayload) -> str:
    # Every field, defaults and nulls included, so equal payloads dump equally.
    # stdlib json, not orjson: digests must match other writers of this
    # canonical form, and orjson encodes non-ASCII text differently.
    return json.dumps(
        payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    )


def _digest(canonical: str) -> str:
    return hashlib.sha256(canonical.encode()).hexdigest()


def payload_hash(payload: CommandPayload) -> str:
    """SHA-256 of the payload's JSON dump with sorted keys, compact separators."""
    return _digest(_canonical(payload))


class PgSessionCommandStore:
    """``SessionCommandStore`` on Postgres.

    Holds a session factory, never a request session, so one instance can serve
    every caller. Each method runs in its own short transaction and commits it.
    The factory decides the RLS context of the sessions it opens. Its sessions
    must use READ COMMITTED (the Postgres default): admission reads after
    taking the root lock and must see rows committed while it waited.
    """

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    async def admit(self, command: SessionCommand) -> Admission:
        """Admit a command, or return the row already admitted for its request ID.

        Raises:
            SessionCommandConflictError: The request ID was admitted for another
                command: a different payload or target session.
            RootSessionNotFoundError: The root session does not exist.
            SessionCommandTypeMismatchError: ``type`` and ``payload.type``
                disagree.
        """
        if command.type != command.payload.type:
            raise SessionCommandTypeMismatchError(
                f"Command type {command.type!r} does not match its"
                f" {command.payload.type!r} payload"
            )
        root_session_id = command.root_session_id
        canonical = _canonical(command.payload)
        digest = _digest(canonical)
        # Store exactly what was hashed, even if the caller mutates a nested value.
        payload: dict[str, JsonValue] = json.loads(canonical)
        async with self._session_factory() as session:
            await session.execute(_LOCK, {"root": str(root_session_id)})
            existing = await session.scalar(
                select(AgentSessionCommand).where(
                    AgentSessionCommand.root_session_id == root_session_id,
                    AgentSessionCommand.request_id == command.request_id,
                )
            )
            if existing is not None:
                # The payload's type is part of its hash.
                if (
                    existing.payload_hash != digest
                    or existing.session_id != command.session_id
                ):
                    raise SessionCommandConflictError(
                        "Request ID was already admitted for another command"
                    )
                return Admission(row=_to_row(existing), existing=True)

            workspace_id = await session.scalar(
                select(AgentSession.workspace_id).where(
                    AgentSession.id == root_session_id
                )
            )
            if workspace_id is None:
                raise RootSessionNotFoundError("Root session does not exist")

            # Retention keeps the newest row, so max + 1 never reuses a seq.
            seq = await session.scalar(
                select(func.coalesce(func.max(AgentSessionCommand.seq), 0) + 1).where(
                    AgentSessionCommand.root_session_id == root_session_id
                )
            )
            created = await session.scalar(
                insert(AgentSessionCommand)
                .values(
                    root_session_id=root_session_id,
                    session_id=command.session_id,
                    workspace_id=workspace_id,
                    request_id=command.request_id,
                    seq=seq,
                    type=command.type,
                    turn_id=command.turn_id,
                    generation=command.generation,
                    payload=payload,
                    payload_hash=digest,
                    state=command.state,
                )
                .returning(AgentSessionCommand)
            )
            if created is None:
                raise RuntimeError("Insert did not return the admitted command")
            row = _to_row(created)
            # Identifiers only; a listener reads the rows itself after waking.
            await session.execute(
                select(func.pg_notify(command_channel(root_session_id), str(row.seq)))
            )
            await session.commit()
        return Admission(row=row, existing=False)

    async def get(
        self, root_session_id: uuid.UUID, request_id: str
    ) -> SessionCommandRow | None:
        """The command admitted for a request ID, if any."""
        async with self._session_factory() as session:
            command = await session.scalar(
                select(AgentSessionCommand).where(
                    AgentSessionCommand.root_session_id == root_session_id,
                    AgentSessionCommand.request_id == request_id,
                )
            )
            return None if command is None else _to_row(command)

    async def pending(self, root_session_id: uuid.UUID) -> list[SessionCommandRow]:
        """Commands that are not terminal, in ``seq`` order."""
        async with self._session_factory() as session:
            result = await session.scalars(
                select(AgentSessionCommand)
                .where(
                    AgentSessionCommand.root_session_id == root_session_id,
                    AgentSessionCommand.state.in_(ACTIVE_STATES),
                )
                .order_by(AgentSessionCommand.seq)
            )
            return [_to_row(command) for command in result]

    async def rows(
        self, root_session_id: uuid.UUID, *, generation: uuid.UUID
    ) -> list[SessionCommandRow]:
        """The commands of one turn generation, in every state, in ``seq`` order."""
        async with self._session_factory() as session:
            result = await session.scalars(
                select(AgentSessionCommand)
                .where(
                    AgentSessionCommand.root_session_id == root_session_id,
                    AgentSessionCommand.generation == generation,
                )
                .order_by(AgentSessionCommand.seq)
            )
            return [_to_row(command) for command in result]

    async def mark(
        self,
        root_session_id: uuid.UUID,
        request_id: str,
        state: CommandState,
        receipt: CommandReceipt | None = None,
        *,
        turn_id: uuid.UUID | None = None,
        generation: uuid.UUID | None = None,
    ) -> SessionCommandRow | None:
        """Move a command forward to ``state``; a state never moves back.

        A move to the current non-terminal state is allowed and records
        ``receipt``. A ``receipt`` of None keeps the stored one. ``rejected``
        and ``withdrawn`` are reachable only from ``queued`` or ``accepted``.

        ``turn_id`` and ``generation`` bind a queued command to the turn that
        takes it. Each is set only while still null, so a command never moves
        to another turn.

        Returns:
            The row as stored, unchanged if the move is not allowed, or None if
            no such command exists. Callers compare its state to see whether
            the move took.
        """
        where = (
            AgentSessionCommand.root_session_id == root_session_id,
            AgentSessionCommand.request_id == request_id,
        )
        advance = (
            update(AgentSessionCommand)
            .where(*where, AgentSessionCommand.state.in_(_FROM_STATES[state]))
            .values(state=state, updated_at=func.now())
            .returning(AgentSessionCommand)
            .execution_options(synchronize_session=False)
        )
        if receipt is not None:
            advance = advance.values(receipt=receipt)
        if turn_id is not None:
            advance = advance.values(
                turn_id=func.coalesce(AgentSessionCommand.turn_id, turn_id)
            )
        if generation is not None:
            advance = advance.values(
                generation=func.coalesce(AgentSessionCommand.generation, generation)
            )
        async with self._session_factory() as session:
            updated = await session.scalar(advance)
            if updated is not None:
                row = _to_row(updated)
                await session.commit()
                return row
            stored = await session.scalar(select(AgentSessionCommand).where(*where))
            return None if stored is None else _to_row(stored)

    async def retain(
        self, root_session_id: uuid.UUID, now: datetime | None = None
    ) -> int:
        """Delete terminal commands last updated more than 24 hours ago.

        Kept regardless of age: the root's newest command, so ``seq`` keeps
        counting up, and every command of a generation that still has a
        non-terminal command, since a turn can outlive the retention window.

        Returns:
            The number of commands deleted.
        """
        cutoff = (now or datetime.now(UTC)) - RETENTION
        newest = aliased(AgentSessionCommand)
        newest_seq = (
            select(func.max(newest.seq))
            .where(newest.root_session_id == root_session_id)
            .scalar_subquery()
        )
        active = aliased(AgentSessionCommand)
        generation_is_active = exists().where(
            active.root_session_id == root_session_id,
            active.generation == AgentSessionCommand.generation,
            active.state.in_(ACTIVE_STATES),
        )
        async with self._session_factory() as session:
            result = await session.scalars(
                delete(AgentSessionCommand)
                .where(
                    AgentSessionCommand.root_session_id == root_session_id,
                    AgentSessionCommand.state.in_(TERMINAL_STATES),
                    AgentSessionCommand.updated_at < cutoff,
                    AgentSessionCommand.seq < newest_seq,
                    ~generation_is_active,
                )
                .returning(AgentSessionCommand.id)
                .execution_options(synchronize_session=False)
            )
            deleted = len(result.all())
            await session.commit()
        return deleted


def _to_row(command: AgentSessionCommand) -> SessionCommandRow:
    return SessionCommandRow(
        id=command.id,
        root_session_id=command.root_session_id,
        session_id=command.session_id,
        workspace_id=command.workspace_id,
        request_id=command.request_id,
        seq=command.seq,
        type=CommandType(command.type),
        turn_id=command.turn_id,
        generation=command.generation,
        payload=COMMAND_PAYLOAD.validate_python(command.payload),
        payload_hash=command.payload_hash,
        state=CommandState(command.state),
        receipt=command.receipt,
        created_at=command.created_at,
        updated_at=command.updated_at,
    )
