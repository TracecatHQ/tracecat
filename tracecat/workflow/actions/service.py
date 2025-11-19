from collections.abc import Sequence

from sqlalchemy import select

from tracecat.db.models import Action
from tracecat.identifiers import ActionID, WorkflowID, WorkflowUUID
from tracecat.service import BaseWorkspaceService
from tracecat.workflow.actions.schemas import ActionCreate, ActionUpdate


class WorkflowActionService(BaseWorkspaceService):
    """Service for managing actions."""

    service_name = "workflow_actions"

    async def list_actions(self, workflow_id: WorkflowID) -> Sequence[Action]:
        statement = select(Action).where(
            Action.owner_id == self.workspace_id,
            Action.workflow_id == workflow_id,
        )
        result = await self.session.execute(statement)
        return result.scalars().all()

    async def get_action(
        self, action_id: ActionID, workflow_id: WorkflowID
    ) -> Action | None:
        statement = select(Action).where(
            Action.owner_id == self.workspace_id,
            Action.id == action_id,
            Action.workflow_id == workflow_id,
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def create_action(self, params: ActionCreate) -> Action:
        action = Action(
            owner_id=self.workspace_id,
            workflow_id=WorkflowUUID.new(params.workflow_id),
            type=params.type,
            title=params.title,
            description=params.description,
            inputs=params.inputs,
            control_flow=params.control_flow.model_dump()
            if params.control_flow
            else {},
            is_interactive=params.is_interactive,
            interaction=params.interaction.model_dump() if params.interaction else None,
        )
        self.session.add(action)
        await self.session.commit()
        await self.session.refresh(action)
        return action

    async def update_action(self, action: Action, params: ActionUpdate) -> Action:
        set_fields = params.model_dump(exclude_unset=True)
        for field, value in set_fields.items():
            setattr(action, field, value)
        self.session.add(action)
        await self.session.commit()
        await self.session.refresh(action)
        return action

    async def delete_action(self, action: Action) -> None:
        await self.session.delete(action)
        await self.session.commit()
