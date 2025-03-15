from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from temporalio import workflow
from temporalio.exceptions import ApplicationError

from tracecat.ee.interactions.enums import InteractionStatus
from tracecat.ee.interactions.models import (
    InteractionInput,
    InteractionResult,
    InteractionState,
)

if TYPE_CHECKING:
    from tracecat.dsl.workflow import DSLWorkflow


class InteractionManager:
    """Manages interactions for a workflow."""

    def __init__(self, workflow: DSLWorkflow) -> None:
        self.wf = workflow
        self.states: dict[uuid.UUID, InteractionState] = {}

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
        if self.wf.wf_exec_id != input.execution_id:
            raise ValueError(
                "Workflow interaction handler received invalid execution ID"
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
        self.wf.logger.info(
            "Received interaction", id=input.interaction_id, action_ref=input.action_ref
        )
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

    async def wait_for_response(
        self, interaction_id: uuid.UUID, timeout: float | None = None
    ) -> dict[str, Any]:
        """Handle a wait response action within the workflow.

        Args:
            task: The action statement containing wait response parameters

        Returns:
            The interaction response data

        Raises:
            ApplicationError: If the interaction times out or encounters an error
        """

        self.wf.logger.info("Waiting for response", interaction_id=interaction_id)
        try:
            self.states[interaction_id].status = InteractionStatus.PENDING
            await workflow.wait_condition(
                lambda: self.states[interaction_id].is_activated(),
                timeout=timeout,
            )
        except TimeoutError as e:
            raise ApplicationError(
                "Timeout waiting for response", non_retryable=True
            ) from e
        except Exception as e:
            self.wf.logger.error(
                "Error waiting for response", interaction_id=interaction_id, exc=e
            )
            raise e

        self.wf.logger.info("Received response", interaction_id=interaction_id)
        return self.states[interaction_id].data
