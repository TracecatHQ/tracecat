from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from pydantic import JsonValue

from tracecat.agent.control.enums import CommandState, CommandType
from tracecat.exceptions import TracecatException
from tracecat.identifiers import WorkspaceID

type CommandPayload = dict[str, JsonValue]
"""Backend-defined JSON object; the store only stores and hashes it."""


@dataclass(frozen=True, slots=True)
class SessionCommand:
    """A command a backend asks the store to admit."""

    root_session_id: uuid.UUID
    session_id: uuid.UUID
    request_id: str
    type: CommandType
    payload: CommandPayload
    # A command starts queued (not yet bound to a turn) or accepted for a turn.
    state: Literal[CommandState.QUEUED, CommandState.ACCEPTED]
    turn_id: uuid.UUID | None = None
    generation: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class SessionCommandRow:
    """An admitted command as stored."""

    id: uuid.UUID
    root_session_id: uuid.UUID
    session_id: uuid.UUID
    workspace_id: WorkspaceID
    request_id: str
    seq: int
    type: CommandType
    turn_id: uuid.UUID | None
    generation: uuid.UUID | None
    payload: CommandPayload
    payload_hash: str
    state: CommandState
    receipt: CommandPayload | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class Admission:
    """The outcome of admitting a command."""

    row: SessionCommandRow
    # True when the request ID was already admitted with the same payload.
    existing: bool


class SessionCommandConflictError(TracecatException):
    """The request ID was already admitted for a different command."""


class RootSessionNotFoundError(TracecatException):
    """The command names a root session that does not exist."""


class SessionCommandStore(Protocol):
    """Ordered, idempotent command queue for an agent session tree."""

    async def admit(self, command: SessionCommand) -> Admission:
        """Admit a command, or return the row already admitted for its request ID.

        Raises:
            SessionCommandConflictError: The request ID was admitted for another
                command: a different payload, type or target session.
            RootSessionNotFoundError: The root session does not exist.
        """
        ...

    async def get(
        self, root_session_id: uuid.UUID, request_id: str
    ) -> SessionCommandRow | None:
        """The command admitted for a request ID, if any."""
        ...

    async def pending(self, root_session_id: uuid.UUID) -> list[SessionCommandRow]:
        """Commands that are not terminal, in ``seq`` order."""
        ...

    async def rows(
        self, root_session_id: uuid.UUID, *, generation: uuid.UUID
    ) -> list[SessionCommandRow]:
        """The commands of one turn generation, in every state, in ``seq`` order."""
        ...

    async def mark(
        self,
        root_session_id: uuid.UUID,
        request_id: str,
        state: CommandState,
        receipt: CommandPayload | None = None,
        *,
        turn_id: uuid.UUID | None = None,
        generation: uuid.UUID | None = None,
    ) -> SessionCommandRow | None:
        """Move a command forward to ``state``; a state never moves back.

        ``rejected`` and ``withdrawn`` are reachable only from ``queued`` or
        ``accepted``.
        ``turn_id`` and ``generation`` bind a queued command to its turn and are
        set only while still null. Returns the row as stored, unchanged if the
        move is not allowed, or None if no such command exists.
        """
        ...

    async def retain(
        self, root_session_id: uuid.UUID, now: datetime | None = None
    ) -> int:
        """Delete expired terminal commands and return how many were deleted.

        Keeps the root's newest command and every command of a generation that
        still has a non-terminal command.
        """
        ...
