"""Contracts shared by built-in and separately installed agent backends."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from temporalio import workflow

from tracecat.agent.backends.schemas import (
    WorkflowApprovalSubmission,
    WorkflowCancelRequest,
)
from tracecat.agent.types import AgentConfig
from tracecat.auth.types import Role
from tracecat.db.models import AgentSession, AgentSessionHistory


class SessionDispatchUncertain(RuntimeError):
    """Dispatch may have succeeded; the caller must retain turn ownership.

    Raise after ownership is committed when a lost acknowledgement leaves the
    dispatch outcome unknown. Definitive pre-dispatch failures use normal errors.
    """


class AgentControlRejected(RuntimeError):
    """A control operation failed definitively; its local attempt may be rolled back."""


class AgentControlUncertain(RuntimeError):
    """A control operation may have applied; preserve its identity for retries."""


@dataclass(frozen=True, slots=True)
class SessionForkContext:
    """Authorized parent and new session for backend-specific fork preparation."""

    db: AsyncSession
    parent: AgentSession
    fork: AgentSession
    role: Role


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


class AgentWorkflow[InputT, OutputT](Protocol):
    """Common execution and control contract for agent workflows."""

    async def run(self, args: InputT, /) -> OutputT: ...

    @workflow.update
    def set_approvals(self, submission: WorkflowApprovalSubmission) -> bool: ...

    @workflow.update
    def request_cancel(self, request: WorkflowCancelRequest) -> None: ...
