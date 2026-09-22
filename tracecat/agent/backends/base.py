"""Shared session reservation and Temporal dispatch for agent backends."""

from abc import ABC, abstractmethod
from dataclasses import replace
from typing import Any, ClassVar
from uuid import UUID

from sqlalchemy import select
from temporalio.client import Client
from temporalio.common import Priority, RetryPolicy, WorkflowIDReusePolicy
from temporalio.workflow import UpdateMethodMultiParam

from tracecat.agent.backends.schemas import WorkflowApprovalSubmission
from tracecat.agent.backends.types import (
    AgentBackendCapability,
    AgentWorkflow,
    SessionDispatchUncertain,
    SessionHistoryAdapter,
    SessionTurnContext,
)
from tracecat.db.models import AgentSession
from tracecat.dsl.client import get_temporal_client
from tracecat.exceptions import TracecatConflictError


class AgentBackend[InputT, OutputT](ABC):
    """Stateless backend with a typed workflow and a shared dispatch lifecycle.

    Factories return one instance per process. Request-scoped database sessions
    and identities must never be retained on the backend.
    """

    # This class attribute depends on the generic input/output types, so Python's
    # typing rules do not allow wrapping it in ClassVar.
    workflow: type[AgentWorkflow[InputT, OutputT]]
    name: ClassVar[str]
    default_harness: ClassVar[str]
    supported_harnesses: ClassVar[frozenset[str]]
    capabilities: ClassVar[frozenset[AgentBackendCapability]] = frozenset()
    history: ClassVar[SessionHistoryAdapter | None] = None
    task_queue: ClassVar[str]
    priority: ClassVar[Priority] = Priority()
    retry_policy: ClassVar[RetryPolicy] = RetryPolicy(maximum_attempts=1)
    id_reuse_policy: ClassVar[WorkflowIDReusePolicy] = (
        WorkflowIDReusePolicy.REJECT_DUPLICATE
    )

    def is_enabled(self) -> bool:
        """Whether this backend can execute new turns."""
        return True

    def workflow_id(self, run_id: UUID) -> str:
        """Build the stable workflow identity for a turn."""
        return f"agent/{run_id}"

    @property
    @abstractmethod
    def approval_update(
        self,
    ) -> UpdateMethodMultiParam[[Any, WorkflowApprovalSubmission], bool]:
        """Return the unbound Temporal update method accepting approval decisions."""

    @abstractmethod
    async def build_workflow_args(self, context: SessionTurnContext) -> InputT:
        """Prepare workflow input and any history writes before reservation commits.

        The session is locked. Implementations must not commit or dispatch work;
        the shared lifecycle commits their writes with turn ownership.
        """

    async def start_turn(self, context: SessionTurnContext) -> None:
        """Reserve the session, commit ownership, and start its typed workflow."""
        client = await get_temporal_client()
        session = await context.db.scalar(
            select(AgentSession)
            .where(
                AgentSession.id == context.session.id,
                AgentSession.workspace_id == context.role.workspace_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if session is None or session.curr_run_id is not None:
            raise TracecatConflictError("This chat already has an active turn")
        args = await self.build_workflow_args(replace(context, session=session))
        session.curr_run_id = context.run_id
        session.active_stream_id = context.stream_id
        session.last_error = None
        context.db.add(session)
        await context.db.commit()
        try:
            await client.start_workflow(
                self.workflow.run,
                args,
                id=self.workflow_id(context.run_id),
                task_queue=self.task_queue,
                retry_policy=self.retry_policy,
                id_reuse_policy=self.id_reuse_policy,
                priority=self.priority,
                search_attributes=context.search_attributes,
            )
        except Exception:
            pass
        else:
            return
        # Raise outside the handler so SDK context cannot leak. Ownership stays
        # reserved because a lost acknowledgement cannot prove dispatch failed.
        raise SessionDispatchUncertain("Dispatch requires reconciliation")

    @abstractmethod
    async def cancel(self, client: Client, run_id: UUID) -> None:
        """Cancel a running turn through the backend's control contract."""
