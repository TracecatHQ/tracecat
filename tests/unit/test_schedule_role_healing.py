from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tracecat.auth.types import Role
from tracecat.dsl.common import DSLRunArgs
from tracecat.dsl.workflow import DSLWorkflow
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.workflow.schedules.service import WorkflowSchedulesService


@pytest.mark.anyio
async def test_get_workspace_organization_id_activity_returns_match(monkeypatch):
    workspace_id = uuid.uuid4()
    organization_id = uuid.uuid4()

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = organization_id
    mock_session.execute.return_value = mock_result

    @asynccontextmanager
    async def fake_session_manager():
        yield mock_session

    monkeypatch.setattr(
        "tracecat.workflow.schedules.service.get_async_session_context_manager",
        fake_session_manager,
    )

    result = await WorkflowSchedulesService.get_workspace_organization_id_activity(
        workspace_id
    )

    assert result == organization_id


@pytest.mark.anyio
async def test_get_workspace_organization_id_activity_returns_none_when_missing(
    monkeypatch,
):
    workspace_id = uuid.uuid4()

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session.execute.return_value = mock_result

    @asynccontextmanager
    async def fake_session_manager():
        yield mock_session

    monkeypatch.setattr(
        "tracecat.workflow.schedules.service.get_async_session_context_manager",
        fake_session_manager,
    )

    result = await WorkflowSchedulesService.get_workspace_organization_id_activity(
        workspace_id
    )

    assert result is None


@pytest.mark.anyio
async def test_dsl_workflow_auto_heals_missing_organization_id():
    workspace_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    role = Role(
        type="service",
        service_id="tracecat-schedule-runner",
        workspace_id=workspace_id,
    )
    args = DSLRunArgs(role=role, wf_id=WorkflowUUID.new_uuid4())

    fake_wf_info = SimpleNamespace(
        workflow_id="wf-test",
        run_id="run-test",
        run_timeout=None,
        execution_timeout=None,
        task_timeout=None,
        retry_policy=None,
        get_current_history_length=lambda: 0,
        get_current_history_size=lambda: 0,
    )

    with (
        patch("tracecat.dsl.workflow.workflow.info", return_value=fake_wf_info),
        patch(
            "tracecat.dsl.workflow.workflow.execute_activity",
            new=AsyncMock(return_value=organization_id),
        ),
    ):
        dsl_workflow = DSLWorkflow(args)
        await dsl_workflow._heal_role_organization_id_if_missing()

    assert dsl_workflow.role.organization_id == organization_id


@pytest.mark.anyio
async def test_dsl_workflow_auto_heal_skips_when_workspace_missing():
    role = Role(type="service", service_id="tracecat-schedule-runner")
    args = DSLRunArgs(role=role, wf_id=WorkflowUUID.new_uuid4())

    fake_wf_info = SimpleNamespace(
        workflow_id="wf-test",
        run_id="run-test",
        run_timeout=None,
        execution_timeout=None,
        task_timeout=None,
        retry_policy=None,
        get_current_history_length=lambda: 0,
        get_current_history_size=lambda: 0,
    )
    mock_execute_activity = AsyncMock()

    with (
        patch("tracecat.dsl.workflow.workflow.info", return_value=fake_wf_info),
        patch(
            "tracecat.dsl.workflow.workflow.execute_activity",
            new=mock_execute_activity,
        ),
    ):
        dsl_workflow = DSLWorkflow(args)
        await dsl_workflow._heal_role_organization_id_if_missing()

    assert dsl_workflow.role.organization_id is None
    mock_execute_activity.assert_not_called()
