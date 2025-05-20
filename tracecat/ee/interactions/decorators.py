from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from tracecat.contexts import ctx_interaction
from tracecat.dsl.models import ActionStatement, TaskResult
from tracecat.ee.interactions.models import (
    InteractionContext,
    ResponseInteraction,
)

if TYPE_CHECKING:
    from tracecat.dsl.workflow import DSLWorkflow


def maybe_interactive(
    func: Callable[..., Awaitable[TaskResult]],
) -> Callable[[DSLWorkflow, ActionStatement], Awaitable[TaskResult]]:
    """Decorator that manages interactivity for a task execution.

    Args:
        task: The action statement being executed

    Returns:
        Decorator function that wraps the task execution
    """

    async def wrapper(wf: DSLWorkflow, task: ActionStatement) -> TaskResult:
        match task.interaction:
            case ResponseInteraction():
                # We only support response interactions for now
                # Open an interaction context
                interaction_id = await wf.interactions.prepare_interaction(
                    action_ref=task.ref,
                    action_type=task.action,
                    interaction_type=task.interaction.type,
                )
                context = InteractionContext(
                    interaction_id=interaction_id,
                    execution_id=wf.wf_exec_id,
                    action_ref=task.ref,
                )
                token = ctx_interaction.set(context)
                try:
                    action_result = await func(wf, task)
                finally:
                    ctx_interaction.reset(token)
                # Apply the wait condition
                interaction_result = await wf.interactions.wait_for_response(
                    interaction_id=interaction_id,
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
