"""Graph service for workflow graph operations.

Implements the canonical graph API with optimistic concurrency.
"""

from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import column, delete, func, literal, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import selectinload

from tracecat.db.models import Action, Workflow
from tracecat.dsl.view import RFGraph
from tracecat.identifiers import WorkflowID
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.service import BaseWorkspaceService
from tracecat.workflow.management.schemas import (
    AddEdgePayload,
    AddNodePayload,
    DeleteEdgePayload,
    DeleteNodePayload,
    GraphOperation,
    GraphResponse,
    MoveNodesPayload,
    UpdateNodePayload,
    UpdateTriggerPositionPayload,
    UpdateViewportPayload,
)


class WorkflowGraphService(BaseWorkspaceService):
    """Service for graph operations on workflows."""

    service_name = "workflow_graph"

    _STRUCTURAL_OPS = {
        "add_node",
        "update_node",
        "delete_node",
        "add_edge",
        "delete_edge",
    }

    async def get_graph(self, workflow_id: WorkflowID) -> GraphResponse | None:
        """Get the canonical graph projection from Actions.

        Builds the graph using RFGraph.from_actions() - Actions are the
        single source of truth.
        """
        workflow_uuid = WorkflowUUID.new(workflow_id)

        # Load workflow with actions
        stmt = (
            select(Workflow)
            .options(selectinload(Workflow.actions))
            .where(
                Workflow.workspace_id == self.workspace_id,
                Workflow.id == workflow_uuid,
            )
        )
        result = await self.session.execute(stmt)
        workflow = result.scalar_one_or_none()

        if workflow is None:
            return None

        # Build canonical graph from Actions
        graph = RFGraph.from_actions(workflow, workflow.actions)

        return GraphResponse(
            version=workflow.graph_version,
            nodes=[node.model_dump(by_alias=True) for node in graph.nodes],
            edges=[edge.model_dump(by_alias=True) for edge in graph.edges],
            viewport={
                "x": workflow.viewport_x,
                "y": workflow.viewport_y,
                "zoom": workflow.viewport_zoom,
            },
        )

    async def apply_operations(
        self,
        workflow_id: WorkflowID,
        base_version: int,
        operations: list[GraphOperation],
    ) -> GraphResponse | None:
        """Apply graph operations with optimistic concurrency.

        Returns 409 if base_version doesn't match current graph_version.
        """
        workflow_uuid = WorkflowUUID.new(workflow_id)

        # Load workflow with FOR UPDATE lock
        stmt = (
            select(Workflow)
            .options(selectinload(Workflow.actions))
            .where(
                Workflow.workspace_id == self.workspace_id,
                Workflow.id == workflow_uuid,
            )
            .with_for_update()
        )
        result = await self.session.execute(stmt)
        workflow = result.scalar_one_or_none()

        if workflow is None:
            return None

        # Check version for optimistic concurrency
        if workflow.graph_version != base_version:
            # Return 409 with latest graph
            graph = RFGraph.from_actions(workflow, workflow.actions)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "message": "Version conflict",
                    "current_version": workflow.graph_version,
                    "graph": GraphResponse(
                        version=workflow.graph_version,
                        nodes=[node.model_dump(by_alias=True) for node in graph.nodes],
                        edges=[edge.model_dump(by_alias=True) for edge in graph.edges],
                    ).model_dump(),
                },
            )

        # Apply operations
        should_bump_version = False
        for op in operations:
            should_bump_version = should_bump_version or self._is_structural(op)
            await self._apply_operation(workflow, op)

        # Increment graph version only for structural mutations
        if should_bump_version:
            workflow.graph_version += 1
            self.session.add(workflow)
        await self.session.commit()

        # Refresh to get updated actions
        await self.session.refresh(workflow, ["actions"])

        # Return updated graph
        graph = RFGraph.from_actions(workflow, workflow.actions)

        return GraphResponse(
            version=workflow.graph_version,
            nodes=[node.model_dump(by_alias=True) for node in graph.nodes],
            edges=[edge.model_dump(by_alias=True) for edge in graph.edges],
            viewport={
                "x": workflow.viewport_x,
                "y": workflow.viewport_y,
                "zoom": workflow.viewport_zoom,
            },
        )

    async def _apply_operation(self, workflow: Workflow, op: GraphOperation) -> None:
        """Apply a single graph operation."""
        match op.type:
            case "add_node":
                await self._add_node(workflow, AddNodePayload(**op.payload))
            case "update_node":
                await self._update_node(workflow, UpdateNodePayload(**op.payload))
            case "delete_node":
                await self._delete_node(workflow, DeleteNodePayload(**op.payload))
            case "add_edge":
                await self._add_edge(workflow, AddEdgePayload(**op.payload))
            case "delete_edge":
                await self._delete_edge(workflow, DeleteEdgePayload(**op.payload))
            case "move_nodes":
                await self._move_nodes(workflow, MoveNodesPayload(**op.payload))
            case "update_trigger_position":
                await self._update_trigger_position(
                    workflow, UpdateTriggerPositionPayload(**op.payload)
                )
            case "update_viewport":
                await self._update_viewport(
                    workflow, UpdateViewportPayload(**op.payload)
                )
            case _:
                raise ValueError(f"Unknown operation type: {op.type}")

    @classmethod
    def _is_structural(cls, op: GraphOperation) -> bool:
        """Return True if the operation mutates graph semantics.

        Layout-only operations (move/update trigger position) do not
        increment graph_version.
        """

        return op.type in cls._STRUCTURAL_OPS

    async def _add_node(self, workflow: Workflow, payload: AddNodePayload) -> None:
        """Add a new action node."""
        action = Action(
            workspace_id=self.workspace_id,
            workflow_id=workflow.id,
            type=payload.type,
            title=payload.title,
            description=payload.description or "",
            inputs=payload.inputs or "",
            control_flow=payload.control_flow or {},
            position_x=payload.position_x,
            position_y=payload.position_y,
            upstream_edges=[],  # New nodes start as entrypoints
        )
        self.session.add(action)
        await self.session.flush()

    async def _update_node(
        self, workflow: Workflow, payload: UpdateNodePayload
    ) -> None:
        """Update an existing action node."""
        update_values = {
            k: v
            for k, v in {
                "title": payload.title,
                "description": payload.description,
                "inputs": payload.inputs,
                "control_flow": payload.control_flow,
            }.items()
            if v is not None
        }

        if not update_values:
            return

        stmt = (
            update(Action)
            .where(
                Action.workspace_id == self.workspace_id,
                Action.workflow_id == workflow.id,
                Action.id == payload.action_id,
            )
            .values(**update_values)
        )
        await self.session.execute(stmt)

    async def _delete_node(
        self, workflow: Workflow, payload: DeleteNodePayload
    ) -> None:
        """Delete an action node and clean up downstream edges."""
        deleted_action_id = str(payload.action_id)

        # Build JSONB subquery to filter out edges referencing deleted action
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

        # Update all actions to remove edges referencing deleted action
        update_stmt = (
            update(Action)
            .where(
                Action.workspace_id == self.workspace_id,
                Action.workflow_id == workflow.id,
            )
            .values(upstream_edges=filtered_edges)
        )
        await self.session.execute(update_stmt)

        # Delete the action
        delete_stmt = delete(Action).where(
            Action.workspace_id == self.workspace_id,
            Action.workflow_id == workflow.id,
            Action.id == payload.action_id,
        )
        await self.session.execute(delete_stmt)

    async def _add_edge(self, workflow: Workflow, payload: AddEdgePayload) -> None:
        """Add an edge between two nodes.

        Supports both trigger and action sources. Validates source exists.
        Normalizes duplicates (only one edge per source_id + source_type).
        """
        # Validate source based on type
        if payload.source_type == "udf":
            # Verify source action exists
            source_exists = await self.session.execute(
                select(Action.id).where(
                    Action.workspace_id == self.workspace_id,
                    Action.workflow_id == workflow.id,
                    Action.id == payload.source_id,
                )
            )
            if source_exists.scalar_one_or_none() is None:
                raise ValueError(f"Source action {payload.source_id} not found")
        elif payload.source_type == "trigger":
            # Validate trigger ID matches workflow
            expected_trigger_id = f"trigger-{workflow.id}"
            if payload.source_id != expected_trigger_id:
                raise ValueError(
                    f"Invalid trigger ID: {payload.source_id}, expected {expected_trigger_id}"
                )

        # Get target action
        target_result = await self.session.execute(
            select(Action).where(
                Action.workspace_id == self.workspace_id,
                Action.workflow_id == workflow.id,
                Action.id == payload.target_id,
            )
        )
        target_action = target_result.scalar_one_or_none()
        if target_action is None:
            raise ValueError(f"Target action {payload.target_id} not found")

        # Build the new edge
        new_edge: dict[str, Any] = {
            "source_id": payload.source_id,
            "source_type": payload.source_type,
        }
        if payload.source_type == "udf":
            new_edge["source_handle"] = payload.source_handle or "success"

        # Filter out existing edge with same source_id + source_type, then add new one
        edges = target_action.upstream_edges or []
        filtered_edges = [
            e
            for e in edges
            if not (
                e.get("source_id") == payload.source_id
                and e.get("source_type") == payload.source_type
            )
        ]
        filtered_edges.append(new_edge)

        target_action.upstream_edges = filtered_edges
        self.session.add(target_action)

    async def _delete_edge(
        self, workflow: Workflow, payload: DeleteEdgePayload
    ) -> None:
        """Delete an edge between two nodes."""
        # Get target action
        target_result = await self.session.execute(
            select(Action).where(
                Action.workspace_id == self.workspace_id,
                Action.workflow_id == workflow.id,
                Action.id == payload.target_id,
            )
        )
        target_action = target_result.scalar_one_or_none()
        if target_action is None:
            raise ValueError(f"Target action {payload.target_id} not found")

        # Remove edge from target's upstream_edges by matching source_id + source_type
        edges = target_action.upstream_edges or []
        target_action.upstream_edges = [
            e
            for e in edges
            if not (
                e.get("source_id") == payload.source_id
                and e.get("source_type") == payload.source_type
            )
        ]
        self.session.add(target_action)

    async def _move_nodes(self, workflow: Workflow, payload: MoveNodesPayload) -> None:
        """Batch update node positions (layout only)."""
        for pos in payload.positions:
            action_id = pos.get("action_id")
            x = pos.get("x")
            y = pos.get("y")
            if action_id is None or x is None or y is None:
                continue

            stmt = (
                update(Action)
                .where(
                    Action.workspace_id == self.workspace_id,
                    Action.workflow_id == workflow.id,
                    Action.id == action_id,
                )
                .values(position_x=x, position_y=y)
            )
            await self.session.execute(stmt)

    async def _update_trigger_position(
        self, workflow: Workflow, payload: UpdateTriggerPositionPayload
    ) -> None:
        """Update trigger node position."""
        workflow.trigger_position_x = payload.x
        workflow.trigger_position_y = payload.y
        self.session.add(workflow)

    async def _update_viewport(
        self, workflow: Workflow, payload: UpdateViewportPayload
    ) -> None:
        """Update viewport position and zoom."""
        workflow.viewport_x = payload.x
        workflow.viewport_y = payload.y
        workflow.viewport_zoom = payload.zoom
        self.session.add(workflow)
