"""Temporal activities for completing case-comment agent invocations."""

from __future__ import annotations

from dataclasses import replace

from temporalio import activity
from tracecat_ee.agent.workflows.durable import AgentWorkflowArgs

from tracecat.agent.schemas import RunAgentArgs
from tracecat.agent.session.service import AgentSessionService
from tracecat.cases.agent_invocations.completion import (
    CaseCommentAgentInvocationCompletionService,
)
from tracecat.cases.agent_invocations.dispatcher import (
    CaseCommentAgentInvocationDispatcher,
)
from tracecat.cases.agent_invocations.schemas import (
    CompleteCommentAgentInvocationInput,
    CompleteCommentAgentInvocationResult,
    FailCommentAgentInvocationInput,
    FailCommentAgentInvocationResult,
    PrepareCommentAgentInvocationInput,
    PrepareCommentAgentInvocationResult,
)
from tracecat.cases.agent_invocations.service import (
    CaseCommentAgentInvocationService,
)
from tracecat.contexts import ctx_role
from tracecat.logger import logger

_MAX_STORED_ERROR_LENGTH = 2_000
_COMMENT_REPLY_ACTIONS = {
    "core.cases.create_comment",
    "core.cases.reply_to_comment",
}


def _without_comment_reply_actions(actions: list[str] | None) -> list[str] | None:
    if actions is None:
        return None
    return [action for action in actions if action not in _COMMENT_REPLY_ACTIONS]


@activity.defn
async def prepare_comment_agent_invocation_activity(
    input: PrepareCommentAgentInvocationInput,
) -> PrepareCommentAgentInvocationResult:
    """Create/link the session and prepare its first child workflow turn."""
    ctx_role.set(input.role)
    async with CaseCommentAgentInvocationDispatcher.with_session(
        role=input.role
    ) as dispatcher:
        prepared = await dispatcher.create_or_get_agent_session(input.invocation_id)
        if prepared is None:
            return PrepareCommentAgentInvocationResult()
        prepared_turn = await AgentSessionService(
            dispatcher.session,
            input.role,
        ).prepare_new_turn(prepared.session_id, prepared.prompt)
        config = replace(
            prepared_turn.config,
            actions=_without_comment_reply_actions(prepared_turn.config.actions),
        )
        workflow_args = AgentWorkflowArgs(
            role=input.role,
            agent_args=RunAgentArgs(
                user_prompt=prepared_turn.prompt,
                session_id=prepared_turn.session_id,
                active_stream_id=prepared_turn.active_stream_id,
                curr_run_id=prepared_turn.run_id,
                config=config,
            ),
            title=prepared_turn.title,
            entity_type=prepared_turn.entity_type,
            entity_id=prepared_turn.entity_id,
            tools=_without_comment_reply_actions(prepared_turn.tools),
            agent_preset_id=prepared_turn.agent_preset_id,
            agent_preset_version_id=prepared_turn.agent_preset_version_id,
        )
    return PrepareCommentAgentInvocationResult(workflow_args=workflow_args)


@activity.defn
async def fail_comment_agent_invocation_activity(
    input: FailCommentAgentInvocationInput,
) -> FailCommentAgentInvocationResult:
    """Idempotently persist a terminal parent-workflow failure."""
    ctx_role.set(input.role)
    message = f"{input.kind}: {input.error}"[:_MAX_STORED_ERROR_LENGTH]
    async with CaseCommentAgentInvocationService.with_session(
        role=input.role
    ) as service:
        await service.claim_pending(input.invocation_id)
        invocation = await service.mark_failed(input.invocation_id, message)
        await service.session.commit()
    return FailCommentAgentInvocationResult(transitioned=invocation is not None)


@activity.defn
async def complete_comment_agent_invocation_activity(
    input: CompleteCommentAgentInvocationInput,
) -> CompleteCommentAgentInvocationResult:
    """Post the terminal agent output as an idempotent case-comment reply."""
    ctx_role.set(input.role)
    try:
        async with CaseCommentAgentInvocationCompletionService.with_session(
            role=input.role
        ) as service:
            reply = await service.create_reply_and_mark_succeeded(
                input.session_id,
                input.output,
            )
    except Exception as exc:
        logger.warning(
            "Failed to complete case comment agent invocation",
            session_id=str(input.session_id),
            run_id=str(input.run_id),
            error=str(exc),
        )
        raise

    return CompleteCommentAgentInvocationResult(
        handled=reply is not None,
        reply_comment_id=reply.id if reply is not None else None,
    )
