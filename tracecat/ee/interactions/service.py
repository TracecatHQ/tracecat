from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from sqlmodel import select
from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError

from tracecat.db.schemas import Interaction
from tracecat.ee.interactions.enums import InteractionStatus, InteractionType
from tracecat.ee.interactions.models import (
    CreateInteractionActivityInputs,
    InteractionCreate,
    InteractionInput,
    InteractionResult,
    InteractionState,
    InteractionUpdate,
    UpdateInteractionActivityInputs,
)
from tracecat.service import BaseWorkspaceService

if TYPE_CHECKING:
    from tracecat.dsl.workflow import DSLWorkflow


class InteractionManager:
    """Manages interactions for a workflow."""

    def __init__(self, workflow: DSLWorkflow) -> None:
        self.wf = workflow
        # DB interaction states are the source of truth, but we still need to track
        # the state of the interaction in the workflow
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
        return InteractionResult(message="success", detail=input.data)

    async def prepare_interaction(
        self,
        action_ref: str,
        action_type: str,
        interaction_type: InteractionType,
    ) -> uuid.UUID:
        # Create an interaction record in the database
        # Create an idle interaction state if it doesn't exist
        interaction_id = await workflow.execute_activity(
            InteractionService.create_interaction_activity,
            arg=CreateInteractionActivityInputs(
                role=self.wf.role,
                params=InteractionCreate(
                    wf_exec_id=self.wf.wf_exec_id,
                    action_ref=action_ref,
                    action_type=action_type,
                    type=interaction_type,
                    status=InteractionStatus.IDLE,
                ),
            ),
            start_to_close_timeout=timedelta(seconds=10),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
        self.wf.logger.warning(
            "Created interaction",
            id=interaction_id,
            action_ref=action_ref,
            action_type=action_type,
            type=interaction_type,
        )
        self.states[interaction_id] = InteractionState(
            type=interaction_type,
            action_ref=action_ref,
            status=InteractionStatus.IDLE,
        )
        return interaction_id

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
            await self._update_interaction(
                interaction_id=interaction_id,
                params=InteractionUpdate(status=InteractionStatus.PENDING),
            )
            await workflow.wait_condition(
                # This state needs to be locally tracked
                lambda: self.states[interaction_id].is_activated(),
                timeout=timeout,
            )
            # Complete the interaction
            await self._update_interaction(
                interaction_id=interaction_id,
                params=InteractionUpdate(
                    status=InteractionStatus.COMPLETED,
                    response_payload=self.states[interaction_id].data,
                ),
            )
            self.wf.logger.info("Received response", interaction_id=interaction_id)
            return self.states[interaction_id].data
        except TimeoutError as e:
            await self._update_interaction(
                interaction_id=interaction_id,
                params=InteractionUpdate(status=InteractionStatus.TIMED_OUT),
            )
            self.wf.logger.error(
                "Timeout waiting for response",
                interaction_id=interaction_id,
                exc=e,
            )
            raise ApplicationError(
                "Timeout waiting for response", non_retryable=True
            ) from e
        except Exception as e:
            await self._update_interaction(
                interaction_id=interaction_id,
                params=InteractionUpdate(status=InteractionStatus.ERROR),
            )
            self.wf.logger.error(
                "Error waiting for response", interaction_id=interaction_id, exc=e
            )
            raise e

    async def _update_interaction(
        self, interaction_id: uuid.UUID, params: InteractionUpdate
    ) -> uuid.UUID:
        return await workflow.execute_activity(
            InteractionService.update_interaction_activity,
            arg=UpdateInteractionActivityInputs(
                role=self.wf.role, interaction_id=interaction_id, params=params
            ),
            start_to_close_timeout=timedelta(seconds=10),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )


class InteractionService(BaseWorkspaceService):
    service_name = "interactions"

    async def create_interaction(self, params: InteractionCreate) -> Interaction:
        """Create a new interaction record in the database.

        Args:
            params: Parameters for creating the interaction

        Returns:
            The created interaction
        """
        interaction = Interaction(
            wf_exec_id=params.wf_exec_id,
            action_ref=params.action_ref,
            action_type=params.action_type,
            type=params.type,
            status=params.status,
            request_payload=params.request_payload,
            response_payload=params.response_payload,
            expires_at=params.expires_at,
            actor=params.actor,
            owner_id=self.workspace_id,
        )
        self.session.add(interaction)
        await self.session.commit()
        await self.session.refresh(interaction)
        return interaction

    async def get_interaction(self, interaction_id: uuid.UUID) -> Interaction | None:
        """Get an interaction by ID.

        Args:
            interaction_id: UUID of the interaction to retrieve

        Returns:
            The interaction if found, None otherwise
        """
        statement = select(Interaction).where(
            Interaction.owner_id == self.workspace_id,
            Interaction.id == interaction_id,
        )
        result = await self.session.exec(statement)
        return result.first()

    async def update_interaction(
        self, interaction: Interaction, params: InteractionUpdate
    ) -> Interaction:
        """Update an existing interaction.

        Args:
            interaction_id: UUID of the interaction to update
            params: Update parameters

        Returns:
            Updated interaction if found, None otherwise
        """
        update_data = params.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(interaction, key, value)

        self.session.add(interaction)
        await self.session.commit()
        await self.session.refresh(interaction)
        return interaction

    async def list_interactions(
        self, *, wf_exec_id: str | None = None
    ) -> Sequence[Interaction]:
        """List all interactions for a workflow execution.

        Args:
            wf_exec_id: Workflow execution ID to filter by

        Returns:
            Sequence of interactions for the workflow
        """
        statement = select(Interaction).where(Interaction.owner_id == self.workspace_id)
        if wf_exec_id:
            statement = statement.where(Interaction.wf_exec_id == wf_exec_id)
        result = await self.session.exec(statement)
        return result.all()

    async def delete_interaction(self, interaction: Interaction) -> None:
        """Delete an interaction by ID.

        Args:
            interaction: The interaction to delete
        """
        await self.session.delete(interaction)
        await self.session.commit()

    @staticmethod
    @activity.defn
    async def create_interaction_activity(
        input: CreateInteractionActivityInputs,
    ) -> uuid.UUID:
        """Create a new interaction record in the database.

        Args:
            params: Parameters for creating the interaction
        """
        async with InteractionService.with_session(role=input.role) as service:
            interaction = await service.create_interaction(input.params)
            service.logger.warning(
                "Created interaction in activity", interaction_id=interaction.id
            )
            return interaction.id

    @staticmethod
    @activity.defn
    async def update_interaction_activity(
        input: UpdateInteractionActivityInputs,
    ) -> uuid.UUID:
        """Update an existing interaction.

        Args:
            input: Parameters for updating the interaction

        Returns:
            The updated interaction
        """
        async with InteractionService.with_session(role=input.role) as service:
            interaction = await service.get_interaction(input.interaction_id)
            if interaction is None:
                raise ApplicationError("Interaction not found", non_retryable=True)
            await service.update_interaction(interaction, input.params)
            return interaction.id
