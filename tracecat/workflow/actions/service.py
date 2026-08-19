from collections.abc import Sequence

from sqlalchemy import column, delete, func, literal, select, update
from sqlalchemy.dialects.postgresql import JSONB

from tracecat.authz.controls import require_scope
from tracecat.db.models import Action, Workflow
from tracecat.dsl.view import Position
from tracecat.identifiers import ActionID, WorkflowID, WorkflowUUID
from tracecat.service import BaseWorkspaceService
from tracecat.workflow.actions.schemas import (
    ActionCreate,
    ActionPositionUpdate,
    ActionUpdate,
)


class WorkflowActionService(BaseWorkspaceService):
    """Service for managing actions."""

    service_name = "workflow_actions"

    async def list_actions(self, workflow_id: WorkflowID) -> Sequence[Action]:
        statement = select(Action).where(
            Action.workspace_id == self.workspace_id,
            Action.workflow_id == workflow_id,
        )
        result = await self.session.execute(statement)
        return result.scalars().all()

    async def get_action(
        self, action_id: ActionID, workflow_id: WorkflowID
    ) -> Action | None:
        statement = select(Action).where(
            Action.workspace_id == self.workspace_id,
            Action.id == action_id,
            Action.workflow_id == workflow_id,
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    @require_scope("workflow:create")
    async def create_action(self, params: ActionCreate) -> Action:
        action = Action(
            workspace_id=self.workspace_id,
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
            position_x=params.position_x,
            position_y=params.position_y,
            upstream_edges=params.upstream_edges,
        )
        self.session.add(action)
        await self.session.commit()
        await self.session.refresh(action)
        return action

    @require_scope("workflow:update")
    async def update_action(self, action: Action, params: ActionUpdate) -> Action:
        set_fields = params.model_dump(exclude_unset=True)
        for field, value in set_fields.items():
            setattr(action, field, value)
        self.session.add(action)
        await self.session.commit()
        await self.session.refresh(action)
        return action

    @require_scope("workflow:delete")
    async def delete_action(self, action: Action) -> None:
        """Delete an action and clean up downstream edge references.

        When deleting a node, we must remove all references to it from
        downstream actions' upstream_edges to maintain graph consistency.

        Uses a single JSONB UPDATE query to filter out edges referencing
        the deleted action, followed by the DELETE - all in one transaction.
        """
        deleted_action_id = str(action.id)
        workflow_id = action.workflow_id

        # Build JSONB subquery to filter out edges referencing deleted action
        # This is equivalent to:
        #   SELECT COALESCE(jsonb_agg(elem), '[]'::jsonb)
        #   FROM jsonb_array_elements(upstream_edges) AS elem
        #   WHERE elem->>'source_id' <> :deleted_action_id
        elem = (
            func.jsonb_array_elements(Action.upstream_edges)
            .table_valued(column("value", JSONB))
            .alias("elem")
        )

        filtered_edges = (
            select(
                func.coalesce(
                    func.jsonb_agg(elem.c.value),
                    literal([], type_=JSONB),
                )
            )
            .where(elem.c.value["source_id"].astext != deleted_action_id)
            .correlate(Action)
            .scalar_subquery()
        )

        # Update all actions in the workflow to remove edges referencing deleted action
        update_stmt = (
            update(Action)
            .where(
                Action.workspace_id == self.workspace_id,
                Action.workflow_id == workflow_id,
            )
            .values(upstream_edges=filtered_edges)
        )
        await self.session.execute(update_stmt)

        # Delete the action
        delete_stmt = delete(Action).where(
            Action.workspace_id == self.workspace_id,
            Action.workflow_id == workflow_id,
            Action.id == action.id,
        )
        await self.session.execute(delete_stmt)

        await self.session.commit()

    @require_scope("workflow:update")
    async def batch_update_positions(
        self,
        workflow_id: WorkflowID,
        action_positions: list[ActionPositionUpdate],
        trigger_position: Position | None = None,
    ) -> None:
        """Batch update action positions and optionally the trigger position.

        This method updates all positions in a single transaction for atomicity,
        preventing race conditions from concurrent position updates.
        """
        workflow_uuid = WorkflowUUID.new(workflow_id)

        # Update action positions
        for pos in action_positions:
            stmt = (
                update(Action)
                .where(
                    Action.workspace_id == self.workspace_id,
                    Action.workflow_id == workflow_uuid,
                    Action.id == pos.action_id,
                )
                .values(position_x=pos.position.x, position_y=pos.position.y)
            )
            await self.session.execute(stmt)

        # Update trigger position if provided
        if trigger_position is not None:
            trigger_update = {}
            if trigger_position.x is not None:
                trigger_update["trigger_position_x"] = trigger_position.x
            if trigger_position.y is not None:
                trigger_update["trigger_position_y"] = trigger_position.y

            stmt = (
                update(Workflow)
                .where(
                    Workflow.workspace_id == self.workspace_id,
                    Workflow.id == workflow_uuid,
                )
                .values(**trigger_update)
            )
            await self.session.execute(stmt)

        await self.session.commit()
