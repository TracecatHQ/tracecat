"""Temporal activities for completing case-comment agent invocations."""

from __future__ import annotations

from dataclasses import replace

from temporalio import activity
from temporalio.exceptions import ApplicationError

from tracecat.agent.backends.registry import find_agent_backend
from tracecat.agent.backends.turns import build_child_agent_turn
from tracecat.agent.error_policy import agent_provider_credentials_missing
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
from tracecat.cases.agent_invocations.types import CaseCommentAgentInvocationError
from tracecat.contexts import ctx_role
from tracecat.exceptions import (
    AgentProviderCredentialsNotFoundError,
    EntitlementRequired,
    TracecatConflictError,
    TracecatValidationError,
)
from tracecat.logger import logger
from tracecat.temporal.errors import raise_application_error_from_classification

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
        try:
            prepared = await dispatcher.create_or_get_agent_session(input.invocation_id)
        except TracecatConflictError as e:
            # The invoking comment was deleted; retrying cannot help.
            raise ApplicationError(
                str(e), type=e.__class__.__name__, non_retryable=True
            ) from e
        if prepared is None:
            return PrepareCommentAgentInvocationResult()
        session_service = AgentSessionService(
            dispatcher.session,
            input.role,
        )
        try:
            prepared_turn = await session_service.prepare_new_turn(
                prepared.session_id, prepared.prompt
            )
        except EntitlementRequired as e:
            # Retrying cannot help until the entitlement is restored.
            raise ApplicationError(
                str(e), e.detail, type=e.__class__.__name__, non_retryable=True
            ) from e
        except AgentProviderCredentialsNotFoundError as e:
            # The organization must add credentials; retrying cannot help.
            raise_application_error_from_classification(
                agent_provider_credentials_missing(e)
            )
        config = replace(
            prepared_turn.config,
            actions=_without_comment_reply_actions(prepared_turn.config.actions),
        )
        turn = await build_child_agent_turn(
            prepared_turn,
            role=session_service.execution_role,
            config=config,
            tools=_without_comment_reply_actions(prepared_turn.tools),
        )
    return PrepareCommentAgentInvocationResult(turn=turn)


@activity.defn
async def fail_comment_agent_invocation_activity(
    input: FailCommentAgentInvocationInput,
) -> FailCommentAgentInvocationResult:
    """Idempotently persist a terminal parent-workflow failure."""
    ctx_role.set(input.role)
    error: CaseCommentAgentInvocationError = {
        "kind": input.kind,
        "message": input.error[:_MAX_STORED_ERROR_LENGTH],
    }
    async with CaseCommentAgentInvocationService.with_session(
        role=input.role
    ) as service:
        await service.claim_pending(input.invocation_id)
        invocation = await service.mark_failed(input.invocation_id, error)
        await service.session.commit()
    return FailCommentAgentInvocationResult(transitioned=invocation is not None)


@activity.defn
async def complete_comment_agent_invocation_activity(
    input: CompleteCommentAgentInvocationInput,
) -> CompleteCommentAgentInvocationResult:
    """Post the terminal agent output as an idempotent case-comment reply."""
    ctx_role.set(input.role)
    output = input.output
    if input.backend_id is not None:
        # Read results of a backend that was disabled mid-turn too.
        backend = find_agent_backend(input.backend_id)
        if backend is None:
            raise TracecatValidationError("Agent backend is unavailable")
        result = backend.parse_turn_result(input.output)
        if result.error is not None:
            return CompleteCommentAgentInvocationResult(
                handled=False, turn_error=result.error
            )
        output = result.output
    try:
        async with CaseCommentAgentInvocationCompletionService.with_session(
            role=input.role
        ) as service:
            reply = await service.create_reply_and_mark_succeeded(
                input.session_id,
                output,
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
