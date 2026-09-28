"""Post-commit delivery for case-comment agent invocations."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError

from tracecat import config
from tracecat.auth.types import Role
from tracecat.cases.agent_invocations.schemas import (
    CaseCommentAgentInvocationWorkflowInput,
    comment_agent_invocation_workflow_id,
)
from tracecat.cases.agent_invocations.service import (
    CaseCommentAgentInvocationService,
)
from tracecat.cases.agent_invocations.workflows import (
    CaseCommentAgentInvocationWorkflow,
)
from tracecat.db.session_events import AfterCommitQueue
from tracecat.dsl.client import get_temporal_client
from tracecat.dsl.common import RETRY_POLICIES
from tracecat.logger import logger

_MAX_STORED_ERROR_LENGTH = 2_000


async def _record_startup_failure(
    invocation_id: uuid.UUID,
    error: Exception,
    role: Role,
) -> None:
    error_message = str(error).strip() or type(error).__name__
    async with CaseCommentAgentInvocationService.with_session(role=role) as service:
        # A pre-session failure rolls the original claim back to pending. Claim it
        # again so the same running -> failed guard handles both startup windows.
        await service.claim_pending(invocation_id)
        await service.mark_failed(
            invocation_id,
            {
                "kind": "startup",
                "message": error_message[:_MAX_STORED_ERROR_LENGTH],
            },
        )
        await service.session.commit()


def invoke_comment_agent_turns_after_commit(
    session: AsyncSession,
    *,
    invocation_ids: Sequence[uuid.UUID],
    role: Role,
) -> None:
    """Dispatch comment invocations only after their transaction commits."""
    pending_ids = tuple(invocation_ids)
    if not pending_ids:
        return

    async def _dispatch_all() -> None:
        for invocation_id in pending_ids:
            try:
                client = await get_temporal_client()
                await client.start_workflow(
                    CaseCommentAgentInvocationWorkflow.run,
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
            except Exception as startup_error:
                logger.exception(
                    "Failed to start case comment agent invocation workflow",
                    invocation_id=str(invocation_id),
                    workspace_id=str(role.workspace_id),
                    error=str(startup_error),
                )
                try:
                    await _record_startup_failure(
                        invocation_id,
                        startup_error,
                        role,
                    )
                except Exception as persistence_error:
                    logger.exception(
                        "Failed to persist case comment agent invocation failure",
                        invocation_id=str(invocation_id),
                        workspace_id=str(role.workspace_id),
                        error=str(persistence_error),
                    )

    AfterCommitQueue.of(session).add(_dispatch_all)
