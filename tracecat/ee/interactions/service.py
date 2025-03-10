from __future__ import annotations

from typing import TYPE_CHECKING, Any

from temporalio import workflow
from temporalio.exceptions import ApplicationError

from tracecat.ee.enums import PlatformAction
from tracecat.ee.interactions.enums import InteractionStatus
from tracecat.ee.interactions.models import (
    InteractionInput,
    InteractionResult,
    InteractionState,
    WaitResponseArgs,
)

if TYPE_CHECKING:
    from tracecat.dsl.common import DSLInput
    from tracecat.dsl.models import ActionStatement
    from tracecat.dsl.workflow import DSLWorkflow


class InteractionManager:
    """Manages interactions for a workflow."""

    def __init__(self, workflow: DSLWorkflow) -> None:
        self.wf = workflow
        self.states: dict[str, InteractionState] = {}

    def prepare_states(self, dsl: DSLInput) -> None:
        """Prepare interaction states for DSL actions.

        Args:
            dsl: The DSL input containing workflow actions

        Returns:
            Dictionary mapping interaction references to their states
        """
        for action in dsl.actions:
            if action.action == PlatformAction.WAIT_RESPONSE:
                act_args = WaitResponseArgs.model_validate(action.args)
                self.states[act_args.ref] = InteractionState(
                    ref=action.ref,
                    type=PlatformAction.WAIT_RESPONSE,
                )

    def validate_interaction(self, input: InteractionInput) -> None:
        """Validate that a received interaction matches its expected state.

        Args:
            input: The interaction handler input to validate

        Raises:
            ValueError: If the interaction state cannot be found or is invalid
        """
        if input.interaction_id not in self.states:
            raise ValueError(
                "Workflow interaction handler could not find interaction state"
            )
        state = self.states[input.interaction_id]
        if state.ref != input.ref:
            raise ValueError(
                "Workflow interaction handler received invalid interaction"
            )

    def handle_interaction(self, input: InteractionInput) -> InteractionResult:
        """Process a received interaction in the workflow.

        Args:
            input: The interaction handler input to process

        Returns:
            The interaction handler result containing the processed data

        Raises:
            ApplicationError: If the interaction is unknown
        """
        self.wf.logger.info("Received interaction", input=input)
        if input.interaction_id not in self.states:
            self.wf.logger.warning(
                "Received interaction for unknown action",
                interaction_id=input.interaction_id,
            )
            raise ApplicationError(
                "Received interaction for unknown action", non_retryable=True
            )

        self.states[input.interaction_id].data = input.data
        self.states[input.interaction_id].status = InteractionStatus.COMPLETED
        return InteractionResult(
            message="success",
            detail=input.data,
        )

    """Actions"""

    async def wait_for_response(self, task: ActionStatement) -> dict[str, Any]:
        """Handle a wait response action within the workflow.

        Args:
            task: The action statement containing wait response parameters

        Returns:
            The interaction response data

        Raises:
            ApplicationError: If the interaction times out or encounters an error
        """
        if task.action != PlatformAction.WAIT_RESPONSE:
            raise ValueError("Task is not a wait response action")

        args = WaitResponseArgs.model_validate(task.args)
        ref = args.ref

        self.wf.logger.warning("Waiting for response", interaction_ref=ref)
        try:
            self.states[ref].status = InteractionStatus.PENDING
            await workflow.wait_condition(
                lambda: self.states[ref].is_activated(),
                timeout=args.timeout,
            )
        except TimeoutError as e:
            raise ApplicationError(
                "Timeout waiting for response", non_retryable=True
            ) from e
        except Exception as e:
            self.wf.logger.error(
                "Error waiting for response", interaction_ref=ref, exc=e
            )
            raise e

        self.wf.logger.warning("Received response", interaction_ref=ref)
        return self.states[ref].data
