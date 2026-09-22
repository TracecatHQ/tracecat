"""Shared session reservation and Temporal dispatch for agent backends."""

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any, ClassVar
from uuid import UUID

from sqlalchemy import select
from temporalio.client import (
    Client,
    WorkflowExecutionStatus,
    WorkflowUpdateRPCTimeoutOrCancelledError,
)
from temporalio.common import (
    Priority,
    RetryPolicy,
    TypedSearchAttributes,
    WorkflowIDReusePolicy,
)
from temporalio.service import RPCError
from temporalio.workflow import UpdateMethodMultiParam

from tracecat.agent.backends.schemas import WorkflowApprovalSubmission
from tracecat.agent.backends.types import (
    AgentBackendCapability,
    AgentControlRejected,
    AgentControlUncertain,
    AgentWorkflow,
    SessionDispatchUncertain,
    SessionHistoryAdapter,
    SessionTurnContext,
)
from tracecat.agent.session.types import TurnLifecycle
from tracecat.db.models import AgentSession
from tracecat.dsl.client import get_temporal_client
from tracecat.exceptions import TracecatConflictError
from tracecat.logger import logger
from tracecat.workflow.executions.correlation import build_agent_session_correlation_id
from tracecat.workflow.executions.enums import (
    ExecutionType,
    TemporalSearchAttr,
    TriggerType,
)


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
    def _approval_update(
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
        search_attributes = self._search_attributes(context)
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
                search_attributes=search_attributes,
            )
        except Exception:
            pass
        else:
            return
        # Raise outside the handler so SDK context cannot leak. Ownership stays
        # reserved because a lost acknowledgement cannot prove dispatch failed.
        raise SessionDispatchUncertain("Dispatch requires reconciliation")

    @staticmethod
    def _search_attributes(context: SessionTurnContext) -> TypedSearchAttributes:
        """Encode metadata for a direct session turn; child callers own theirs."""
        pairs = [
            TriggerType.MANUAL.to_temporal_search_attr_pair(),
            ExecutionType.PUBLISHED.to_temporal_search_attr_pair(),
            TemporalSearchAttr.CORRELATION_ID.create_pair(
                build_agent_session_correlation_id(context.session.id)
            ),
        ]
        if context.role.user_id is not None:
            pairs.append(
                TemporalSearchAttr.TRIGGERED_BY_USER_ID.create_pair(
                    str(context.role.user_id)
                )
            )
        if context.role.workspace_id is not None:
            pairs.append(
                TemporalSearchAttr.WORKSPACE_ID.create_pair(
                    str(context.role.workspace_id)
                )
            )
        return TypedSearchAttributes(search_attributes=pairs)

    async def _run_control[ResultT](
        self, operation: Callable[[Client], Awaitable[ResultT]]
    ) -> ResultT:
        """Keep transport errors private and distinguish safe rollback from uncertainty."""
        started = False
        try:
            client = await get_temporal_client()
            started = True
            return await operation(client)
        except (WorkflowUpdateRPCTimeoutOrCancelledError, RPCError):
            error = AgentControlUncertain if started else AgentControlRejected
        except Exception:
            error = AgentControlRejected
        # Leave the handler before raising so SDK exception context cannot leak.
        # Task cancellation propagates unchanged: it never proves rejection.
        if error is AgentControlUncertain:
            raise AgentControlUncertain("Agent control outcome is uncertain")
        raise AgentControlRejected("Agent control operation failed")

    async def submit_approvals(
        self, run_id: UUID, submission: WorkflowApprovalSubmission
    ) -> bool:
        """Submit decisions idempotently; False means accepted but still waiting.

        Reuse the continuation stream identity after an uncertain outcome.
        Only a definitive rejection permits rolling back that attempt.
        """
        if submission.new_stream_id is None:
            raise AgentControlRejected(
                "Approval continuation requires a stream identity"
            )
        resumed = await self._run_control(
            lambda client: client.get_workflow_handle(
                self.workflow_id(run_id)
            ).execute_update(
                self._approval_update,
                submission,
                id=f"set-approvals:{submission.new_stream_id}",
            )
        )
        return resumed is not False

    async def get_turn_lifecycle(self, run_id: UUID) -> TurnLifecycle:
        """Resolve execution status for reconnect without exposing Temporal types."""
        try:
            description = await self._run_control(
                lambda client: client.get_workflow_handle(
                    self.workflow_id(run_id)
                ).describe()
            )
        except AgentControlUncertain:
            # Preserve reconnect behavior when history is gone or lookup fails.
            logger.warning(
                "Failed to describe agent workflow for reconnect", run_id=str(run_id)
            )
            return TurnLifecycle.FAILED
        match description.status:
            case (
                WorkflowExecutionStatus.RUNNING
                | WorkflowExecutionStatus.CONTINUED_AS_NEW
            ):
                return TurnLifecycle.RUNNING
            case WorkflowExecutionStatus.COMPLETED:
                return TurnLifecycle.COMPLETED
            case WorkflowExecutionStatus.CANCELED:
                return TurnLifecycle.CANCELLED
            case _:
                return TurnLifecycle.FAILED

    async def cancel(self, run_id: UUID) -> None:
        """Request cancellation using this backend's execution controls."""
        await self._run_control(lambda client: self._cancel(client, run_id))

    @abstractmethod
    async def _cancel(self, client: Client, run_id: UUID) -> None:
        """Send implementation-specific cancellation signals and updates."""
