"""Test workflow schedule cleanup functionality."""

import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest

from tracecat.auth.types import Role
from tracecat.db.engine import get_async_session_context_manager
from tracecat.db.models import Schedule, Workflow
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.workflow.management.management import WorkflowsManagementService


@pytest.mark.anyio
async def test_delete_workflow_cleans_up_schedules(test_role: Role):
    """Test that deleting a workflow properly cleans up Temporal schedules."""

    async with get_async_session_context_manager() as session:
        # Create workflow management service
        mgmt_service = WorkflowsManagementService(session, role=test_role)

        # Create a test workflow
        workflow = Workflow(
            title="Test Workflow",
            description="Test workflow for schedule cleanup",
            workspace_id=test_role.workspace_id
            if test_role.workspace_id
            else uuid.uuid4(),
            version=1,
        )
        session.add(workflow)
        await session.commit()
        await session.refresh(workflow)

        # Create test schedules in the database
        schedule1 = Schedule(
            id=uuid.uuid4(),
            workflow_id=workflow.id,
            workspace_id=test_role.workspace_id
            if test_role.workspace_id
            else uuid.uuid4(),
            every=timedelta(hours=1),
            offset=None,
            start_at=None,
            end_at=None,
            timeout=None,
        )
        schedule2 = Schedule(
            id=uuid.uuid4(),
            workflow_id=workflow.id,
            workspace_id=test_role.workspace_id
            if test_role.workspace_id
            else uuid.uuid4(),
            every=timedelta(hours=2),
            offset=None,
            start_at=None,
            end_at=None,
            timeout=None,
        )
        session.add(schedule1)
        session.add(schedule2)
        await session.commit()

        # Mock the Temporal bridge delete_schedule function
        with patch("tracecat.workflow.schedules.bridge.delete_schedule") as mock_delete:
            mock_delete.return_value = None

            # Delete the workflow
            workflow_uuid = WorkflowUUID.new(workflow.id)
            await mgmt_service.delete_workflow(workflow_uuid)

            # Verify that Temporal schedules were deleted
            assert mock_delete.call_count == 2

            # Verify the schedule IDs that were passed to delete_schedule
            called_schedule_ids = {call.args[0] for call in mock_delete.call_args_list}
            expected_schedule_ids = {schedule1.id, schedule2.id}
            assert called_schedule_ids == expected_schedule_ids


@pytest.mark.anyio
async def test_delete_workflow_handles_temporal_errors_gracefully(test_role: Role):
    """Test that workflow deletion continues even if Temporal schedule cleanup fails."""

    async with get_async_session_context_manager() as session:
        # Create workflow management service
        mgmt_service = WorkflowsManagementService(session, role=test_role)

        # Create a test workflow
        workflow = Workflow(
            title="Test Workflow",
            description="Test workflow for error handling",
            workspace_id=test_role.workspace_id
            if test_role.workspace_id
            else uuid.uuid4(),
            version=1,
        )
        session.add(workflow)
        await session.commit()
        await session.refresh(workflow)

        # Create a test schedule
        schedule = Schedule(
            id=uuid.uuid4(),
            workflow_id=workflow.id,
            workspace_id=test_role.workspace_id
            if test_role.workspace_id
            else uuid.uuid4(),
            every=timedelta(hours=1),
            offset=None,
            start_at=None,
            end_at=None,
            timeout=None,
        )
        session.add(schedule)
        await session.commit()

        # Mock the Temporal bridge to raise an exception
        with patch("tracecat.workflow.schedules.bridge.delete_schedule") as mock_delete:
            mock_delete.side_effect = Exception("Temporal connection failed")

            # Delete the workflow - should not raise an exception
            workflow_uuid = WorkflowUUID.new(workflow.id)
            await mgmt_service.delete_workflow(workflow_uuid)

            # Verify that delete_schedule was called despite the error
            mock_delete.assert_called_once_with(schedule.id)


@pytest.mark.anyio
async def test_delete_workflow_with_no_schedules(test_role: Role):
    """Test that deleting a workflow with no schedules works normally."""

    async with get_async_session_context_manager() as session:
        # Create workflow management service
        mgmt_service = WorkflowsManagementService(session, role=test_role)

        # Create a test workflow with no schedules
        workflow = Workflow(
            title="Test Workflow No Schedules",
            description="Test workflow without schedules",
            workspace_id=test_role.workspace_id
            if test_role.workspace_id
            else uuid.uuid4(),
            version=1,
        )
        session.add(workflow)
        await session.commit()
        await session.refresh(workflow)

        # Mock the Temporal bridge
        with patch("tracecat.workflow.schedules.bridge.delete_schedule") as mock_delete:
            # Delete the workflow
            workflow_uuid = WorkflowUUID.new(workflow.id)
            await mgmt_service.delete_workflow(workflow_uuid)

            # Verify that delete_schedule was never called
            mock_delete.assert_not_called()
