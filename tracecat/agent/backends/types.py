"""Contracts shared by built-in and separately installed agent backends."""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from temporalio.common import TypedSearchAttributes

from tracecat.agent.types import AgentConfig
from tracecat.auth.types import Role
from tracecat.db.models import AgentSession, AgentSessionHistory


class AgentBackendCapability(StrEnum):
    """Optional operations an agent backend can perform."""

    FORK = "fork"
    CALLER_OWNED_WORKFLOWS = "caller_owned_workflows"


class SessionDispatchUncertain(RuntimeError):
    """Dispatch may have succeeded; the caller must retain turn ownership.

    Raise after ownership is committed when a lost acknowledgement leaves the
    dispatch outcome unknown. Definitive pre-dispatch failures use normal errors.
    """


@dataclass(frozen=True, slots=True)
class SessionTurnContext:
    """Resolved, authorized inputs for dispatching one session turn."""

    db: AsyncSession
    session: AgentSession
    role: Role
    config: AgentConfig
    prompt: str
    run_id: UUID
    stream_id: UUID
    search_attributes: TypedSearchAttributes


@runtime_checkable
class SessionHistoryAdapter(Protocol):
    """Project native history into the existing Claude-compatible chat format.

    Preserve native records and IDs. Display output must never replace the
    harness's native model history.
    """

    async def load(
        self,
        db: AsyncSession,
        session: AgentSession,
        *,
        approval_tool_call_ids: set[str],
        include_active: bool,
    ) -> Sequence[AgentSessionHistory]: ...

    # Native history and the existing display parser accept arbitrary provider
    # JSON. Keep that boundary opaque rather than pretending it is a shared schema.
    def project(self, entry: AgentSessionHistory) -> dict[str, Any] | None: ...


class AgentWorkflow[InputT, ResultT](Protocol):
    """Typed run method implemented by each concrete Temporal workflow class."""

    async def run(self, args: InputT, /) -> ResultT: ...
