from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from temporalio import workflow

from tracecat.contexts import ctx_interaction
from tracecat.dsl.models import ActionStatement, DSLNodeResult
from tracecat.ee.interactions.enums import InteractionStatus
from tracecat.ee.interactions.models import (
    InteractionContext,
    InteractionState,
    ResponseInteraction,
)

if TYPE_CHECKING:
    from tracecat.dsl.workflow import DSLWorkflow


def maybe_interactive(
    func: Callable[..., Awaitable[DSLNodeResult]],
) -> Callable[[DSLWorkflow, ActionStatement], Awaitable[DSLNodeResult]]:
    """Decorator that manages interactivity for a task execution.

    Args:
        task: The action statement being executed

    Returns:
        Decorator function that wraps the task execution
    """

    async def wrapper(wf: DSLWorkflow, task: ActionStatement) -> DSLNodeResult:
        match task.interaction:
            case ResponseInteraction():
                # We only support response interactions for now
                # Open an interaction context
                interaction_id = workflow.uuid4()
                context = InteractionContext(
                    interaction_id=interaction_id,
                    execution_id=wf.wf_exec_id,
                    action_ref=task.ref,
                )
                # Create an idle interaction state if it doesn't exist
                wf.interactions.states[interaction_id] = InteractionState(
                    type=task.interaction.type,
                    action_ref=task.ref,
                    status=InteractionStatus.IDLE,
                )
                token = ctx_interaction.set(context)
                try:
                    action_result = await func(wf, task)
                finally:
                    ctx_interaction.reset(token)
                # Apply the wait condition
                interaction_result = await wf.interactions.wait_for_response(
                    interaction_id
                )
                action_result.update(
                    interaction=interaction_result,
                    interaction_id=str(interaction_id),
                    interaction_type=str(task.interaction.type),
                )
            case _:
                action_result = await func(wf, task)
        return action_result

    return wrapper
