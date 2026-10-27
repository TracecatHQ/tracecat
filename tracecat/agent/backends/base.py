"""Shared session reservation and Temporal dispatch for agent backends."""

import asyncio
import copy
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from datetime import timedelta
from typing import ClassVar
from uuid import UUID

from sqlalchemy import func, select, update
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
from temporalio.service import RPCError, RPCStatusCode

from tracecat.agent.backends.dispatch import TurnDispatchClient
from tracecat.agent.backends.schemas import (
    WorkflowApprovalSubmission,
    WorkflowCancelRequest,
)
from tracecat.agent.backends.types import (
    AgentControlRejected,
    AgentControlUncertain,
    AgentWorkflow,
    PendingMessage,
    SessionDispatchUncertain,
    SessionForkContext,
    SessionHistoryAdapter,
    SessionTurnContext,
    SessionWorkflowContext,
    TurnResult,
    WithdrawResult,
)
from tracecat.agent.cancellation import signal_turn_cancel
from tracecat.agent.references.contracts import ReferenceCapabilities
from tracecat.agent.session.types import AgentSessionEntity, TurnLifecycle
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

# A tombstone claims the workflow ID of a turn whose start outcome was lost. No
# worker registers this type or polls this queue: the execution only has to exist.
_TOMBSTONE_WORKFLOW = "AgentTurnStartTombstone"
_TOMBSTONE_TASK_QUEUE = "agent-turn-start-tombstone"
# Closes a tombstone whose termination was lost, so the next send can recover.
_TOMBSTONE_TIMEOUT = timedelta(seconds=10)
# sdk-core's default deadline, made explicit because recovery waits it out.
_START_RPC_TIMEOUT = timedelta(seconds=30)
# Until then a missing workflow may be a start still in flight. Tombstoning it
# would drop that turn for a quick second send, so keep the conflict instead.
_TOMBSTONE_MIN_AGE = _START_RPC_TIMEOUT + timedelta(seconds=5)


class AgentBackend[InputT, OutputT](ABC):
    """Stateless backend with a typed workflow and a shared dispatch lifecycle.

    Factories return one instance per process. Request-scoped database sessions
    and identities must never be retained on the backend.
    """

    workflow: type[AgentWorkflow[InputT, OutputT]]
    name: ClassVar[str]
    default_harness: ClassVar[str]
    supported_harnesses: ClassVar[frozenset[str]]
    history: ClassVar[SessionHistoryAdapter | None] = None
    reference_capabilities: ClassVar[tuple[ReferenceCapabilities, ...]] = ()
    # Whether preset subagents may require manual approval. A backend that sets
    # this must hold their tool calls until a decision in the parent turn.
    subagent_approvals: ClassVar[bool] = False
    # Whether a running turn accepts steering messages. A backend that sets this
    # must implement `steer`.
    steering: ClassVar[bool] = False
    # Whether a running turn can take back pending user messages. A backend
    # that sets this must implement `withdraw`.
    withdrawing: ClassVar[bool] = False
    # Whether the backend can list the user's messages the model has not read.
    # A backend that sets this must implement `list_pending`.
    listing_pending: ClassVar[bool] = False
    # Whether a caller may run a turn's workflow as its own child workflow and
    # read the result. A backend that sets this must implement
    # `parse_turn_result`.
    caller_owned_turns: ClassVar[bool] = False
    task_queue: ClassVar[str]
    priority: ClassVar[Priority] = Priority()
    retry_policy: ClassVar[RetryPolicy] = RetryPolicy(maximum_attempts=1)

    def is_enabled(self) -> bool:
        """Whether this backend can execute new turns."""
        return True

    def workflow_id(self, run_id: UUID) -> str:
        """Build the stable workflow identity for a turn."""
        return f"agent/{run_id}"

    @abstractmethod
    async def prepare_fork(self, context: SessionForkContext) -> None:
        """Prepare the fork's native history and state from its parent.

        Every backend must support session forks. The service supplies a flushed,
        uncommitted session with its ID, backend, harness and parent identity set.
        Prepare any backend-specific state using the supplied transaction; do not commit
        or start execution. Reading or executing the fork must preserve the
        parent's history without mutating the parent.
        """

    @abstractmethod
    async def build_workflow_args(self, context: SessionWorkflowContext) -> InputT:
        """Build workflow input without side effects from a data-only snapshot.

        Implementations must not write history or backend state, open write
        transactions, or dispatch work. Persist turn data in workflow activities
        after execution starts, idempotently across activity retries. The shared
        lifecycle alone commits session ownership before the start RPC.
        """

    async def start_turn(self, context: SessionTurnContext) -> None:
        """Reserve the session, commit ownership, and start its typed workflow."""
        client = await get_temporal_client()
        locked_session = (
            select(AgentSession)
            .where(
                AgentSession.id == context.session.id,
                AgentSession.workspace_id == context.role.workspace_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        session = await context.db.scalar(locked_session)
        if session is None:
            raise TracecatConflictError("This chat already has an active turn")
        if session.curr_run_id is not None:
            recovered = await self._release_terminal_turn(
                replace(context, session=session), client
            )
            if not recovered:
                raise TracecatConflictError("This chat already has an active turn")
            # Recovery commits independently so a subsequent preparation failure
            # cannot restore stale ownership. Reacquire the lock before admission.
            session = await context.db.scalar(locked_session)
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

    async def retains_terminal_ownership(
        self, context: SessionTurnContext, run_id: UUID
    ) -> bool:
        """Whether a terminal turn must keep its session reservation.

        Called only after Temporal confirms that the workflow for ``run_id`` is
        terminal or can never start, and before the shared lifecycle releases
        that turn's ownership.
        ``context.session`` is the session row locked for update in
        ``context.db``. Return True when the backend's durable records show that
        the turn ended with an uncertain outcome that must be reconciled before
        another turn; the session then stays owned and the new turn is rejected
        as a conflict. Implementations must be read-only: never write, flush,
        commit, or roll back ``context.db``.

        Args:
            context: The turn being admitted, holding the locked session row.
            run_id: The terminal turn that currently owns the session.

        Returns:
            True to keep the session owned by ``run_id``; False to release it.
        """
        return False

    async def _release_terminal_turn(
        self, context: SessionTurnContext, client: Client
    ) -> bool:
        """Recover a locked reservation only when Temporal proves it cannot run."""
        session = context.session
        run_id = session.curr_run_id
        if run_id is None:
            return False
        try:
            handle = await self.handle(run_id, client=client)
            description = await handle.describe(rpc_timeout=timedelta(seconds=5))
        except RPCError as e:
            if e.status != RPCStatusCode.NOT_FOUND:
                return False
            description = None
        except Exception:
            return False
        if description is None:
            # NOT_FOUND can race with the commit-to-start gap or a start whose
            # acknowledgement was lost. Only claiming the workflow ID proves
            # that the original dispatch can never start.
            if not await self._outlived_start_deadline(context):
                return False
            if not await self._claim_unstarted_turn(run_id, client):
                return False
            last_error = "Agent turn could not be started"
        elif description.status in {
            WorkflowExecutionStatus.COMPLETED,
            WorkflowExecutionStatus.FAILED,
            WorkflowExecutionStatus.CANCELED,
            WorkflowExecutionStatus.TERMINATED,
            WorkflowExecutionStatus.TIMED_OUT,
        }:
            last_error = "Previous agent turn ended before session cleanup completed"
        else:
            return False
        if await self.retains_terminal_ownership(context, run_id):
            return False
        released = await context.db.scalar(
            update(AgentSession)
            .where(
                AgentSession.id == session.id,
                AgentSession.workspace_id == context.role.workspace_id,
                AgentSession.curr_run_id == run_id,
                AgentSession.active_stream_id == session.active_stream_id,
            )
            .values(
                curr_run_id=None,
                active_stream_id=None,
                reserved_run_id=None,
                reserved_at=None,
                last_error=session.last_error or last_error,
            )
            .returning(AgentSession.id)
        )
        await context.db.commit()
        return released is not None

    @staticmethod
    async def _outlived_start_deadline(context: SessionTurnContext) -> bool:
        """Whether direct dispatch made the locked reservation, past its deadline.

        Only that start is bounded by ``_START_RPC_TIMEOUT``. Other writers, such
        as caller-owned child workflows or the agent runtime's queued successor,
        may start long after reserving, so their reservations are never
        tombstoned and conflict until their workflow is terminal.
        """
        outlived = await context.db.scalar(
            select(AgentSession.id).where(
                AgentSession.id == context.session.id,
                AgentSession.workspace_id == context.role.workspace_id,
                AgentSession.reserved_run_id == AgentSession.curr_run_id,
                AgentSession.reserved_at + _TOMBSTONE_MIN_AGE <= func.clock_timestamp(),
            )
        )
        return outlived is not None

    async def _claim_unstarted_turn(self, run_id: UUID, client: Client) -> bool:
        """Claim a missing turn's workflow ID so its original start can never run.

        Every turn starts with ``REJECT_DUPLICATE``, so once any execution holds
        the ID, a delayed start of the original turn is rejected and its prompt
        is never replayed. The tombstone needs no worker and is terminated at once.

        Returns:
            True only when the tombstone started. False when the original start
            already holds the ID, so its terminal status releases the turn later,
            or when the tombstone's own start outcome is unknown.
        """
        try:
            tombstone = await client.start_workflow(
                _TOMBSTONE_WORKFLOW,
                id=self.workflow_id(run_id),
                task_queue=_TOMBSTONE_TASK_QUEUE,
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                execution_timeout=_TOMBSTONE_TIMEOUT,
                rpc_timeout=timedelta(seconds=5),
            )
        except Exception:
            return False
        try:
            await tombstone.terminate(
                reason="Agent turn could not be started",
                rpc_timeout=timedelta(seconds=5),
            )
        except Exception:
            # The ID is already claimed; the execution timeout closes the tombstone.
            logger.warning(
                "Failed to terminate agent turn tombstone", run_id=str(run_id)
            )
        return True

    async def _prepare_and_dispatch_turn(
        self, context: SessionTurnContext, client: Client
    ) -> None:
        """Settle preparation, dispatch, and cleanup while caller cancellation waits."""
        session = context.session
        session_id = session.id
        dispatch = TurnDispatchClient(client.service_client, context.db.commit)
        commit_reconciled = False
        try:
            args = await self.build_workflow_args(
                SessionWorkflowContext(
                    session_id=session.id,
                    backend_id=session.backend_id,
                    harness_type=session.harness_type,
                    title=session.title,
                    entity_type=AgentSessionEntity(session.entity_type),
                    entity_id=session.entity_id,
                    tools=tuple(session.tools) if session.tools is not None else None,
                    agent_preset_id=session.agent_preset_id,
                    agent_preset_version_id=session.agent_preset_version_id,
                    role=context.role.model_copy(deep=True),
                    config=copy.deepcopy(context.config),
                    prompt=context.prompt,
                    run_id=context.run_id,
                    stream_id=context.stream_id,
                )
            )
            search_attributes = self._search_attributes(context)
            session.curr_run_id = context.run_id
            session.active_stream_id = context.stream_id
            session.last_error = None
            # Marks this reservation as one whose start is bounded by a deadline.
            # Flushed by the commit just before the start RPC, so this is when
            # that deadline begins. Recovery waits it out from here.
            session.reserved_run_id = context.run_id
            session.reserved_at = func.clock_timestamp()
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
                # Recovery tombstones a lost start's ID. REJECT_DUPLICATE keeps a
                # delayed start from running after that tombstone has closed.
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                priority=self.priority,
                search_attributes=search_attributes,
                rpc_timeout=_START_RPC_TIMEOUT,
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
                    .values(
                        curr_run_id=None,
                        active_stream_id=None,
                        reserved_run_id=None,
                        reserved_at=None,
                    )
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
                    .values(
                        curr_run_id=None,
                        active_stream_id=None,
                        reserved_run_id=None,
                        reserved_at=None,
                    )
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

    async def steer(self, run_id: UUID, message_id: UUID, text: str) -> bool:
        """Deliver a message to a running turn; False means the turn refused it.

        Idempotency is session-scoped: a ``message_id`` reaches at most one turn
        of the session. A retry returns the first outcome, even when a later
        turn of the same session is now running.
        Only backends that set ``steering`` implement this.
        """
        raise AgentControlRejected("This agent backend does not support steering")

    async def withdraw(
        self, run_id: UUID, request_id: UUID, target_ids: Sequence[UUID] | None
    ) -> WithdrawResult:
        """Take back the user's pending messages that the model has not read.

        ``target_ids`` of None withdraws all of the session's pending steers and
        queued messages. A retry with the same ``request_id`` while a turn of the
        session runs must return the first result; with no running turn the API
        refuses the retry before it reaches the backend.
        Only backends that set ``withdrawing`` implement this.
        """
        raise AgentControlRejected("This agent backend does not support withdrawing")

    async def list_pending(self, session_id: UUID) -> Sequence[PendingMessage]:
        """List the session's user messages the model has not read, in send order.

        Covers steers waiting to join the running turn and messages queued for a
        later turn. Only backends that set ``listing_pending`` implement this.
        """
        raise AgentControlRejected(
            "This agent backend does not support listing pending messages"
        )

    def parse_turn_result(self, result: object) -> TurnResult:
        """Read a finished turn from its workflow's decoded result.

        A caller that runs the turn as its own child workflow starts it by
        workflow type name, so ``result`` is the decoded JSON of the workflow's
        output, not the backend's output type. Report a failed or cancelled turn
        through ``TurnResult.error`` rather than raising. Only backends that set
        ``caller_owned_turns`` implement this.
        """
        raise NotImplementedError(
            "This agent backend does not support caller-owned turns"
        )

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
