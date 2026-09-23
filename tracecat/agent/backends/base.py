"""Shared session reservation and Temporal dispatch for agent backends."""

import asyncio
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import ClassVar
from uuid import UUID

from sqlalchemy import select, update
from temporalio.client import (
    Client,
    WorkflowExecutionStatus,
    WorkflowHandle,
    WorkflowUpdateFailedError,
)
from temporalio.common import (
    Priority,
    RetryPolicy,
    TypedSearchAttributes,
    WorkflowIDReusePolicy,
)
from temporalio.service import RPCError

from tracecat.agent.backends.dispatch import TurnDispatchClient
from tracecat.agent.backends.schemas import (
    WorkflowApprovalSubmission,
    WorkflowCancelRequest,
)
from tracecat.agent.backends.types import (
    AgentBackendCapability,
    AgentControlRejected,
    AgentControlUncertain,
    AgentWorkflow,
    SessionDispatchUncertain,
    SessionHistoryAdapter,
    SessionTurnContext,
)
from tracecat.agent.cancellation import signal_turn_cancel
from tracecat.agent.session.types import TurnLifecycle
from tracecat.concurrency import rejoin_future_on_cancel
from tracecat.contexts import ctx_role
from tracecat.db.engine import get_async_session_context_manager
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

    @abstractmethod
    async def build_workflow_args(self, context: SessionTurnContext) -> InputT:
        """Prepare workflow input and any history writes before reservation commits.

        The session is locked. Implementations must not commit or dispatch work;
        the shared lifecycle commits their writes with turn ownership only after
        Temporal has encoded and validated the start request.
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
        # Once admitted, finish the ownership decision before releasing the
        # request's DB session. Cancellation must not interrupt a commit that
        # PostgreSQL may already have accepted, or skip dispatch/cleanup after it.
        await rejoin_future_on_cancel(
            asyncio.create_task(
                self._prepare_and_dispatch_turn(
                    replace(context, session=session), client
                )
            )
        )

    async def _prepare_and_dispatch_turn(
        self, context: SessionTurnContext, client: Client
    ) -> None:
        """Settle preparation, dispatch, and cleanup while caller cancellation waits."""
        session = context.session
        session_id = session.id
        dispatch = TurnDispatchClient(client.service_client, context.db.commit)
        commit_reconciled = False
        try:
            args = await self.build_workflow_args(context)
            search_attributes = self._search_attributes(context)
            session.curr_run_id = context.run_id
            session.active_stream_id = context.stream_id
            session.last_error = None
            context.db.add(session)
            # Clone the public client configuration to retain codecs and tracing
            # without changing the process-wide client's service connection.
            # Plugin configuration has already been applied to this snapshot;
            # rerunning plugins could replace the request-scoped service client.
            dispatch_client = Client(
                **{**client.config(), "service_client": dispatch, "plugins": []}
            )
            await dispatch_client.start_workflow(
                self.workflow.run,
                args,
                id=self.workflow_id(context.run_id),
                task_queue=self.task_queue,
                retry_policy=self.retry_policy,
                id_reuse_policy=self.id_reuse_policy,
                priority=self.priority,
                search_attributes=search_attributes,
            )
        except asyncio.CancelledError:
            if not dispatch.committed:
                await context.db.rollback()
            raise
        except Exception:
            if not dispatch.committed:
                if dispatch.commit_attempted:
                    commit_reconciled = await self._reconcile_failed_commit(
                        context, session_id
                    )
                else:
                    await context.db.rollback()
        else:
            return
        if not dispatch.committed:
            if dispatch.commit_attempted and not commit_reconciled:
                raise SessionDispatchUncertain("Dispatch requires reconciliation")
            raise RuntimeError("Agent workflow start failed before dispatch")
        if dispatch.rejected:
            # Do this in the shared lifecycle so non-HTTP callers also release
            # rejected turns. Never clear a different turn's reservation.
            try:
                released = await context.db.scalar(
                    update(AgentSession)
                    .where(
                        AgentSession.id == session_id,
                        AgentSession.workspace_id == context.role.workspace_id,
                        AgentSession.curr_run_id == context.run_id,
                        AgentSession.active_stream_id == context.stream_id,
                    )
                    .values(curr_run_id=None, active_stream_id=None)
                    .returning(AgentSession.id)
                )
                await context.db.commit()
            except Exception:
                # Cleanup itself may have an uncertain outcome. Keep the router
                # from attempting a less restrictive second cleanup.
                pass
            else:
                if released is not None:
                    raise RuntimeError("Agent workflow start was rejected")
        # Raise outside the handler so SDK context cannot leak. Ownership stays
        # reserved because a lost acknowledgement cannot prove dispatch failed.
        raise SessionDispatchUncertain("Dispatch requires reconciliation")

    @staticmethod
    async def _reconcile_failed_commit(
        context: SessionTurnContext, session_id: UUID
    ) -> bool:
        """Release only this reservation when commit raised before any start RPC."""
        role_token = ctx_role.set(context.role)
        try:
            # A lost acknowledgement can leave the original session unusable.
            # Release its connection before checking ownership in a fresh session.
            try:
                await context.db.rollback()
            except Exception:
                await context.db.invalidate()
            async with get_async_session_context_manager() as db:
                released = await db.scalar(
                    update(AgentSession)
                    .where(
                        AgentSession.id == session_id,
                        AgentSession.workspace_id == context.role.workspace_id,
                        AgentSession.curr_run_id == context.run_id,
                        AgentSession.active_stream_id == context.stream_id,
                    )
                    .values(curr_run_id=None, active_stream_id=None)
                    .returning(AgentSession.id)
                )
                await db.commit()
            # If ownership changed, suppress the router's less restrictive cleanup.
            return released is not None
        except Exception:
            return False
        finally:
            ctx_role.reset(role_token)

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

    async def handle(
        self, run_id: UUID, *, client: Client | None = None
    ) -> WorkflowHandle[AgentWorkflow[InputT, OutputT], OutputT]:
        """Resolve a typed handle, reusing a client for batched lookups if supplied."""
        if client is None:
            client = await get_temporal_client()
        return client.get_workflow_handle_for(
            self.workflow.run, self.workflow_id(run_id)
        )

    async def _run_control[ResultT](
        self,
        run_id: UUID,
        operation: Callable[
            [WorkflowHandle[AgentWorkflow[InputT, OutputT], OutputT]],
            Awaitable[ResultT],
        ],
    ) -> ResultT:
        """Resolve once, then preserve uncertainty unless execution rejects the update."""
        started = False
        try:
            handle = await self.handle(run_id)
            started = True
            return await operation(handle)
        except WorkflowUpdateFailedError:
            # The server returned a failed update outcome, not a lost response.
            error = AgentControlRejected
        except Exception:
            # Even decoding a successful response can fail after the update has
            # applied. Only failures before handle acquisition are safe to undo.
            error = AgentControlUncertain if started else AgentControlRejected
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
            run_id,
            lambda handle: handle.execute_update(
                self.workflow.set_approvals,
                submission,
                id=f"set-approvals:{submission.new_stream_id}",
            ),
        )
        return resumed is not False

    async def get_turn_lifecycle(self, run_id: UUID) -> TurnLifecycle:
        """Resolve execution status for reconnect without exposing Temporal types."""

        handle = await self.handle(run_id)
        try:
            description = await handle.describe()
        except RPCError:
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
        """Interrupt the live executor and durably request workflow cancellation."""
        try:
            await signal_turn_cancel(str(run_id), reason="user_cancel")
        except Exception:
            # Redis is the fast path; failure must not prevent durable delivery.
            logger.warning("Failed to write turn cancel signal", run_id=str(run_id))

        await self._run_control(
            run_id,
            lambda handle: handle.execute_update(
                self.workflow.request_cancel,
                WorkflowCancelRequest(reason="user_cancel"),
                id=f"cancel:{run_id}",
            ),
        )
