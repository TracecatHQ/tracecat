"""Contracts shared by built-in and separately installed session backends."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from temporalio.client import Client
from temporalio.common import TypedSearchAttributes

from tracecat.agent.types import AgentConfig
from tracecat.auth.types import Role
from tracecat.db.models import AgentSession, AgentSessionHistory


class SessionDispatchUncertain(RuntimeError):
    """Dispatch may have succeeded; the caller must retain turn ownership."""


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
    """Project a backend's opaque persisted history into chat display records."""

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


@runtime_checkable
class SessionBackend(Protocol):
    """Session dispatch and Temporal control; shared services enforce access.

    Factories return process-wide, stateless providers. Request-scoped database
    sessions and identities are passed explicitly and must never be retained.
    All backends use tracecat.agent.workflow_id.agent_workflow_id for Temporal
    identity and preserve the context search attributes when dispatching.
    """

    @property
    def name(self) -> str: ...

    @property
    def default_harness(self) -> str: ...

    @property
    def supported_harnesses(self) -> frozenset[str]: ...

    @property
    def supports_fork(self) -> bool: ...

    @property
    def supports_caller_owned_workflows(self) -> bool: ...

    @property
    def approval_update_name(self) -> str: ...

    @property
    def history(self) -> SessionHistoryAdapter | None: ...

    def is_enabled(self) -> bool: ...

    async def start_turn(self, context: SessionTurnContext) -> None: ...

    async def cancel(self, client: Client, run_id: UUID) -> None: ...
