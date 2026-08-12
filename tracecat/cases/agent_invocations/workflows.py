"""Durable orchestration for case-comment agent invocations."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Literal

from temporalio import workflow
from temporalio.exceptions import ApplicationError, is_cancelled_exception

with workflow.unsafe.imports_passed_through():
    from tracecat_ee.agent.types import AgentWorkflowID
    from tracecat_ee.agent.workflows.durable import DurableAgentWorkflow

    from tracecat import config
    from tracecat.cases.agent_invocations.schemas import (
        CASE_COMMENT_AGENT_INVOCATION_WORKFLOW,
        COMPLETE_COMMENT_AGENT_INVOCATION_ACTIVITY,
        FAIL_COMMENT_AGENT_INVOCATION_ACTIVITY,
        PREPARE_COMMENT_AGENT_INVOCATION_ACTIVITY,
        CaseCommentAgentInvocationWorkflowInput,
        CompleteCommentAgentInvocationInput,
        CompleteCommentAgentInvocationResult,
        FailCommentAgentInvocationInput,
        FailCommentAgentInvocationResult,
        PrepareCommentAgentInvocationInput,
        PrepareCommentAgentInvocationResult,
    )
    from tracecat.cases.agent_invocations.types import (
        CaseCommentAgentInvocationErrorKind,
    )
    from tracecat.dsl.common import RETRY_POLICIES
    from tracecat.logger import logger


@workflow.defn(name=CASE_COMMENT_AGENT_INVOCATION_WORKFLOW)
class CaseCommentAgentInvocationWorkflow:
    """Run an agent child workflow and deliver its output to the comment thread."""

    @workflow.run
    async def run(
        self,
        input: CaseCommentAgentInvocationWorkflowInput,
    ) -> CompleteCommentAgentInvocationResult:
        stage: Literal["preparation", "agent_turn", "completion"] = "preparation"
        try:
            prepared = await workflow.execute_activity(
                PREPARE_COMMENT_AGENT_INVOCATION_ACTIVITY,
                PrepareCommentAgentInvocationInput(
                    role=input.role,
                    invocation_id=input.invocation_id,
                ),
                start_to_close_timeout=timedelta(seconds=60),
                retry_policy=RETRY_POLICIES["activity:fail_slow"],
                result_type=PrepareCommentAgentInvocationResult,
            )
            if prepared.workflow_args is None:
                return CompleteCommentAgentInvocationResult(handled=False)

            run_id = prepared.workflow_args.agent_args.curr_run_id
            if run_id is None:
                raise ApplicationError(
                    "Prepared comment agent turn has no run ID",
                    non_retryable=True,
                )
            stage = "agent_turn"
            output = await workflow.execute_child_workflow(
                DurableAgentWorkflow.run,
                prepared.workflow_args,
                id=AgentWorkflowID(run_id),
                task_queue=config.TRACECAT__AGENT_QUEUE,
                retry_policy=RETRY_POLICIES["workflow:fail_fast"],
            )
            stage = "completion"
            return await workflow.execute_activity(
                COMPLETE_COMMENT_AGENT_INVOCATION_ACTIVITY,
                CompleteCommentAgentInvocationInput(
                    role=input.role,
                    session_id=output.session_id,
                    run_id=run_id,
                    output=output.output,
                ),
                start_to_close_timeout=timedelta(seconds=60),
                retry_policy=RETRY_POLICIES["activity:fail_slow"],
                result_type=CompleteCommentAgentInvocationResult,
            )
        except BaseException as exc:
            kind = "cancelled" if is_cancelled_exception(exc) else stage
            await self._record_failure(input, kind, str(exc))
            raise

    async def _record_failure(
        self,
        input: CaseCommentAgentInvocationWorkflowInput,
        kind: CaseCommentAgentInvocationErrorKind,
        error: str,
    ) -> None:
        """Persist failure without replacing the workflow's original exception."""
        task = asyncio.create_task(
            workflow.execute_activity(
                FAIL_COMMENT_AGENT_INVOCATION_ACTIVITY,
                FailCommentAgentInvocationInput(
                    role=input.role,
                    invocation_id=input.invocation_id,
                    kind=kind,
                    error=error,
                ),
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RETRY_POLICIES["activity:fail_slow"],
                result_type=FailCommentAgentInvocationResult,
            )
        )
        try:
            await asyncio.shield(task)
        except BaseException as cleanup_error:
            if is_cancelled_exception(cleanup_error):
                try:
                    await asyncio.shield(task)
                except BaseException as retry_error:
                    logger.error(
                        "Failed to persist cancelled comment agent invocation",
                        invocation_id=str(input.invocation_id),
                        error=str(retry_error),
                    )
            else:
                logger.error(
                    "Failed to persist comment agent invocation failure",
                    invocation_id=str(input.invocation_id),
                    error=str(cleanup_error),
                )
