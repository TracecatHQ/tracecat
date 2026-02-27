from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from temporalio.exceptions import ApplicationError

from tracecat.auth.types import Role
from tracecat.dsl.common import DSLRunArgs
from tracecat.dsl.workflow import DSLWorkflow
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.workspaces.activities import get_workspace_organization_id_activity


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
        "tracecat.workspaces.activities.get_async_session_context_manager",
        fake_session_manager,
    )

    result = await get_workspace_organization_id_activity(workspace_id)

    assert result == organization_id


@pytest.mark.anyio
async def test_get_workspace_organization_id_activity_raises_when_missing(
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
        "tracecat.workspaces.activities.get_async_session_context_manager",
        fake_session_manager,
    )

    with pytest.raises(ApplicationError, match="not found or has no organization"):
        await get_workspace_organization_id_activity(workspace_id)


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
        await dsl_workflow._resolve_organization_id()

    assert dsl_workflow.role.organization_id == organization_id


@pytest.mark.anyio
async def test_dsl_workflow_init_raises_when_workspace_missing():
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

    with (
        patch("tracecat.dsl.workflow.workflow.info", return_value=fake_wf_info),
        pytest.raises(ApplicationError, match="Workspace ID is required"),
    ):
        DSLWorkflow(args)
