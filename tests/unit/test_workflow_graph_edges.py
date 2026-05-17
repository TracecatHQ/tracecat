"""Tests for workflow graph edge operations.

Covers the dedup contract for `_add_edge` and `_delete_edge`: success and
error edges between the same source/target pair must coexist independently,
and deleting one handle must not affect the other.

Regression for https://github.com/TracecatHQ/tracecat/issues/2619.
"""

import uuid
from collections.abc import AsyncGenerator
from typing import Literal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.db.models import Action, Workflow, Workspace
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.workflow.graph.service import EdgeDedupKey, WorkflowGraphService
from tracecat.workflow.management.schemas import (
    AddEdgePayload,
    DeleteEdgePayload,
    GraphOperation,
    GraphOperationType,
)

pytestmark = pytest.mark.usefixtures("db")

type EdgeHandle = Literal["success", "error"]


@pytest.fixture
async def workflow_pair(
    session: AsyncSession,
    svc_workspace: Workspace,
) -> AsyncGenerator[tuple[Workflow, Action, Action], None]:
    """Workflow with a source action A and a target action B, no edges yet."""
    workflow_id = uuid.uuid4()
    workflow = Workflow(
        id=workflow_id,
        title="Edge Handle Workflow",
        description="Edge handle regression fixture",
        status="offline",
        workspace_id=svc_workspace.id,
        config={},
    )
    session.add(workflow)
    await session.flush()

    action_a = Action(
        id=uuid.uuid4(),
        workspace_id=svc_workspace.id,
        workflow_id=workflow_id,
        type="core.http_request",
        title="Action A",
        description="Source",
        inputs="",
        control_flow={},
        position_x=0,
        position_y=0,
        upstream_edges=[],
    )
    action_b = Action(
        id=uuid.uuid4(),
        workspace_id=svc_workspace.id,
        workflow_id=workflow_id,
        type="core.http_request",
        title="Action B",
        description="Target",
        inputs="",
        control_flow={},
        position_x=100,
        position_y=0,
        upstream_edges=[],
    )
    session.add_all([action_a, action_b])
    await session.commit()
    await session.refresh(workflow, ["actions"])

    try:
        yield workflow, action_a, action_b
    finally:
        await session.refresh(workflow, ["actions"])
        for action in workflow.actions:
            await session.delete(action)
        await session.delete(workflow)
        await session.commit()


def _add_edge_op(
    source_id: uuid.UUID, target_id: uuid.UUID, handle: EdgeHandle
) -> GraphOperation:
    return GraphOperation(
        type=GraphOperationType.ADD_EDGE,
        payload=AddEdgePayload(
            source_id=str(source_id),
            source_type="udf",
            target_id=target_id,
            source_handle=handle,
        ).model_dump(mode="json"),
    )


def _delete_edge_op(
    source_id: uuid.UUID, target_id: uuid.UUID, handle: EdgeHandle
) -> GraphOperation:
    return GraphOperation(
        type=GraphOperationType.DELETE_EDGE,
        payload=DeleteEdgePayload(
            source_id=str(source_id),
            source_type="udf",
            target_id=target_id,
            source_handle=handle,
        ).model_dump(mode="json"),
    )


def _handles_for(action: Action, source_id: uuid.UUID) -> list[str]:
    return sorted(
        str(e.get("source_handle", ""))
        for e in (action.upstream_edges or [])
        if e.get("source_id") == str(source_id)
    )


def test_edge_dedup_key_udf_defaults_to_success() -> None:
    """Missing handle on a udf edge canonicalises to 'success'."""
    assert WorkflowGraphService._edge_dedup_key("a", "udf", None) == EdgeDedupKey(
        "a", "udf", "success"
    )
    assert WorkflowGraphService._edge_dedup_key("a", "udf", "success") == EdgeDedupKey(
        "a", "udf", "success"
    )
    assert WorkflowGraphService._edge_dedup_key("a", "udf", "error") == EdgeDedupKey(
        "a", "udf", "error"
    )


def test_edge_dedup_key_trigger_ignores_handle() -> None:
    """Trigger edges have no handle concept."""
    assert WorkflowGraphService._edge_dedup_key(
        "trigger-x", "trigger", None
    ) == EdgeDedupKey(
        "trigger-x",
        "trigger",
        None,
    )
    assert WorkflowGraphService._edge_dedup_key("trigger-x", "trigger", "success") == (
        EdgeDedupKey("trigger-x", "trigger", None)
    )


@pytest.mark.anyio
async def test_add_edge_success_then_error_coexist(
    session: AsyncSession,
    svc_role: Role,
    workflow_pair: tuple[Workflow, Action, Action],
) -> None:
    """Adding error after success leaves both edges intact (#2619)."""
    workflow, action_a, action_b = workflow_pair
    service = WorkflowGraphService(session, role=svc_role)

    await service.apply_operations(
        WorkflowUUID.new(workflow.id),
        workflow.graph_version,
        [_add_edge_op(action_a.id, action_b.id, "success")],
    )
    await service.apply_operations(
        WorkflowUUID.new(workflow.id),
        workflow.graph_version,
        [_add_edge_op(action_a.id, action_b.id, "error")],
    )

    await session.refresh(action_b)
    assert _handles_for(action_b, action_a.id) == ["error", "success"]


@pytest.mark.anyio
async def test_add_edge_error_then_success_coexist(
    session: AsyncSession,
    svc_role: Role,
    workflow_pair: tuple[Workflow, Action, Action],
) -> None:
    """Insertion order does not matter."""
    workflow, action_a, action_b = workflow_pair
    service = WorkflowGraphService(session, role=svc_role)

    await service.apply_operations(
        WorkflowUUID.new(workflow.id),
        workflow.graph_version,
        [_add_edge_op(action_a.id, action_b.id, "error")],
    )
    await service.apply_operations(
        WorkflowUUID.new(workflow.id),
        workflow.graph_version,
        [_add_edge_op(action_a.id, action_b.id, "success")],
    )

    await session.refresh(action_b)
    assert _handles_for(action_b, action_a.id) == ["error", "success"]


@pytest.mark.anyio
async def test_add_edge_same_handle_is_idempotent(
    session: AsyncSession,
    svc_role: Role,
    workflow_pair: tuple[Workflow, Action, Action],
) -> None:
    """Re-adding the same handle replaces, never duplicates."""
    workflow, action_a, action_b = workflow_pair
    service = WorkflowGraphService(session, role=svc_role)

    await service.apply_operations(
        WorkflowUUID.new(workflow.id),
        workflow.graph_version,
        [_add_edge_op(action_a.id, action_b.id, "success")],
    )
    await service.apply_operations(
        WorkflowUUID.new(workflow.id),
        workflow.graph_version,
        [_add_edge_op(action_a.id, action_b.id, "success")],
    )

    await session.refresh(action_b)
    assert _handles_for(action_b, action_a.id) == ["success"]


@pytest.mark.anyio
async def test_delete_edge_targets_only_matching_handle(
    session: AsyncSession,
    svc_role: Role,
    workflow_pair: tuple[Workflow, Action, Action],
) -> None:
    """Deleting the error edge must leave the success edge intact."""
    workflow, action_a, action_b = workflow_pair
    service = WorkflowGraphService(session, role=svc_role)

    await service.apply_operations(
        WorkflowUUID.new(workflow.id),
        workflow.graph_version,
        [
            _add_edge_op(action_a.id, action_b.id, "success"),
            _add_edge_op(action_a.id, action_b.id, "error"),
        ],
    )
    await session.refresh(action_b)
    assert _handles_for(action_b, action_a.id) == ["error", "success"]

    await service.apply_operations(
        WorkflowUUID.new(workflow.id),
        workflow.graph_version,
        [_delete_edge_op(action_a.id, action_b.id, "error")],
    )

    await session.refresh(action_b)
    assert _handles_for(action_b, action_a.id) == ["success"]


@pytest.mark.anyio
async def test_delete_edge_other_handle_preserved(
    session: AsyncSession,
    svc_role: Role,
    workflow_pair: tuple[Workflow, Action, Action],
) -> None:
    """Deleting success must not also drop error (mirror case)."""
    workflow, action_a, action_b = workflow_pair
    service = WorkflowGraphService(session, role=svc_role)

    await service.apply_operations(
        WorkflowUUID.new(workflow.id),
        workflow.graph_version,
        [
            _add_edge_op(action_a.id, action_b.id, "success"),
            _add_edge_op(action_a.id, action_b.id, "error"),
        ],
    )

    await service.apply_operations(
        WorkflowUUID.new(workflow.id),
        workflow.graph_version,
        [_delete_edge_op(action_a.id, action_b.id, "success")],
    )

    await session.refresh(action_b)
    assert _handles_for(action_b, action_a.id) == ["error"]


@pytest.mark.anyio
async def test_trigger_edges_unaffected_by_handle_dedup(
    session: AsyncSession,
    svc_role: Role,
    workflow_pair: tuple[Workflow, Action, Action],
) -> None:
    """Trigger edges still dedupe by (source_id, source_type) only."""
    workflow, _, action_b = workflow_pair
    service = WorkflowGraphService(session, role=svc_role)

    trigger_id = f"trigger-{workflow.id}"
    op = GraphOperation(
        type=GraphOperationType.ADD_EDGE,
        payload=AddEdgePayload(
            source_id=trigger_id,
            source_type="trigger",
            target_id=action_b.id,
        ).model_dump(mode="json"),
    )

    await service.apply_operations(
        WorkflowUUID.new(workflow.id), workflow.graph_version, [op]
    )
    await service.apply_operations(
        WorkflowUUID.new(workflow.id), workflow.graph_version, [op]
    )

    await session.refresh(action_b)
    trigger_edges = [
        e for e in (action_b.upstream_edges or []) if e.get("source_type") == "trigger"
    ]
    assert len(trigger_edges) == 1
    assert "source_handle" not in trigger_edges[0]


@pytest.mark.anyio
async def test_legacy_edge_without_handle_dedupes_against_success(
    session: AsyncSession,
    svc_role: Role,
    workflow_pair: tuple[Workflow, Action, Action],
) -> None:
    """A legacy udf edge missing source_handle is treated as 'success'."""
    workflow, action_a, action_b = workflow_pair

    # Seed a legacy edge (no source_handle key) directly.
    action_b.upstream_edges = [{"source_id": str(action_a.id), "source_type": "udf"}]
    session.add(action_b)
    await session.commit()
    await session.refresh(action_b)

    service = WorkflowGraphService(session, role=svc_role)
    await service.apply_operations(
        WorkflowUUID.new(workflow.id),
        workflow.graph_version,
        [_add_edge_op(action_a.id, action_b.id, "success")],
    )

    refreshed = await session.execute(select(Action).where(Action.id == action_b.id))
    target = refreshed.scalar_one()
    udf_edges = [
        e
        for e in (target.upstream_edges or [])
        if e.get("source_id") == str(action_a.id)
    ]
    assert len(udf_edges) == 1
    assert udf_edges[0]["source_handle"] == "success"
