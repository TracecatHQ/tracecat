"""Durable delivery for case-comment agent invocations."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio.client import Client
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError

from tracecat import config
from tracecat.auth.types import Role
from tracecat.cases.agent_invocations.schemas import (
    CASE_COMMENT_AGENT_INVOCATION_WORKFLOW,
    CaseCommentAgentInvocationWorkflowInput,
    comment_agent_invocation_workflow_id,
)
from tracecat.cases.enums import CaseCommentAgentInvocationStatus
from tracecat.db.engine import get_async_session_bypass_rls_context_manager
from tracecat.db.models import CaseCommentAgentInvocation
from tracecat.db.session_events import AfterCommitQueue
from tracecat.dsl.client import get_temporal_client
from tracecat.dsl.common import RETRY_POLICIES
from tracecat.logger import logger

_RECONCILE_BATCH_SIZE = 100
_RECONCILE_INTERVAL_SECONDS = 30


async def _start_invocation_workflow(
    client: Client,
    *,
    invocation_id: uuid.UUID,
    role: Role,
) -> None:
    try:
        await client.start_workflow(
            CASE_COMMENT_AGENT_INVOCATION_WORKFLOW,
            CaseCommentAgentInvocationWorkflowInput(
                role=role,
                invocation_id=invocation_id,
            ),
            id=comment_agent_invocation_workflow_id(invocation_id),
            task_queue=config.TRACECAT__AGENT_QUEUE,
            retry_policy=RETRY_POLICIES["workflow:fail_fast"],
            id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
        )
    except WorkflowAlreadyStartedError:
        logger.info(
            "Case comment agent invocation workflow already started",
            invocation_id=str(invocation_id),
            workspace_id=str(role.workspace_id),
        )


async def _load_pending_invocations(
    session: AsyncSession,
) -> list[tuple[uuid.UUID, object]]:
    statement = (
        select(CaseCommentAgentInvocation.id, CaseCommentAgentInvocation.role)
        .where(
            CaseCommentAgentInvocation.status
            == CaseCommentAgentInvocationStatus.PENDING.value
        )
        .order_by(
            CaseCommentAgentInvocation.created_at,
            CaseCommentAgentInvocation.surrogate_id,
        )
        .limit(_RECONCILE_BATCH_SIZE)
    )
    return list((await session.execute(statement)).tuples().all())


async def reconcile_pending_comment_agent_invocations(
    client: Client,
    *,
    session: AsyncSession | None = None,
) -> int:
    """Start a bounded batch of durable pending invocation rows."""
    if session is None:
        async with get_async_session_bypass_rls_context_manager() as owned_session:
            rows = await _load_pending_invocations(owned_session)
    else:
        rows = await _load_pending_invocations(session)

    started = 0
    for invocation_id, role_payload in rows:
        role = Role.model_validate(role_payload)
        try:
            await _start_invocation_workflow(
                client,
                invocation_id=invocation_id,
                role=role,
            )
        except Exception as error:
            logger.exception(
                "Failed to reconcile case comment agent invocation",
                invocation_id=str(invocation_id),
                workspace_id=str(role.workspace_id),
                error=str(error),
            )
        else:
            started += 1
    return started


async def run_comment_agent_invocation_reconciler(
    client: Client,
    shutdown_event: asyncio.Event,
) -> None:
    """Continuously relay pending invocation rows until worker shutdown."""
    while not shutdown_event.is_set():
        try:
            await reconcile_pending_comment_agent_invocations(client)
        except Exception as error:
            logger.exception(
                "Failed to scan pending case comment agent invocations",
                error=str(error),
            )
        try:
            await asyncio.wait_for(
                shutdown_event.wait(),
                timeout=_RECONCILE_INTERVAL_SECONDS,
            )
        except TimeoutError:
            continue


def invoke_comment_agent_turns_after_commit(
    session: AsyncSession,
    *,
    invocation_ids: Sequence[uuid.UUID],
    role: Role,
) -> None:
    """Attempt immediate delivery after commit; pending rows remain recoverable."""
    pending_ids = tuple(invocation_ids)
    if not pending_ids:
        return

    async def _dispatch_all() -> None:
        client = await get_temporal_client()
        for invocation_id in pending_ids:
            try:
                await _start_invocation_workflow(
                    client,
                    invocation_id=invocation_id,
                    role=role,
                )
            except Exception as startup_error:
                # The durable pending row is intentionally retained for the worker
                # reconciler instead of converting a transient delivery error into
                # a terminal invocation failure.
                logger.exception(
                    "Failed to start case comment agent invocation workflow",
                    invocation_id=str(invocation_id),
                    workspace_id=str(role.workspace_id),
                    error=str(startup_error),
                )

    AfterCommitQueue.of(session).add(_dispatch_all)
