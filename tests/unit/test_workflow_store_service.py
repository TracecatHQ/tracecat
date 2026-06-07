"""Tests for WorkflowStoreService publishing behavior."""

import uuid
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock, patch

import pytest

from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.db.models import Workflow
from tracecat.dsl.common import DSLEntrypoint, DSLInput
from tracecat.dsl.schemas import ActionStatement
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.workflow.store.schemas import WorkflowDslPublish, WorkflowDslPublishResult
from tracecat.workflow.store.service import WorkflowStoreService


@pytest.fixture
def workflow_store_service() -> WorkflowStoreService:
    session = AsyncMock()
    session.add = Mock()
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-api"],
    )
    return WorkflowStoreService(session=session, role=role)


@pytest.fixture
def sample_dsl() -> DSLInput:
    return DSLInput(
        title="Test workflow",
        description="A workflow for store publish tests",
        entrypoint=DSLEntrypoint(ref="start", expects={}),
        actions=[
            ActionStatement(
                ref="start",
                action="core.transform.passthrough",
                args={"value": "test"},
            )
        ],
    )


def _workflow_fixture(
    workflow_id: WorkflowUUID,
    *,
    case_trigger: SimpleNamespace | None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=workflow_id,
        alias="test-workflow",
        tags=[],
        folder=None,
        schedules=[],
        webhook=SimpleNamespace(
            methods=["POST"], status="online", include_headers=True
        ),
        case_trigger=case_trigger,
        git_sync_branch=None,
    )


@pytest.mark.anyio
async def test_publish_workflow_uses_workspace_sync_exporter(
    workflow_store_service: WorkflowStoreService,
    sample_dsl: DSLInput,
) -> None:
    workflow_id = WorkflowUUID.new_uuid4()
    workflow = _workflow_fixture(
        workflow_id,
        case_trigger=SimpleNamespace(
            status="offline",
            event_types=[],
            tag_filters=[],
        ),
    )

    with patch("tracecat.workflow.store.service.WorkspaceGitSyncService") as sync_cls:
        sync_service = AsyncMock()
        sync_service.export_workflow_publish_result.return_value = (
            WorkflowDslPublishResult(
                status="no_op",
                commit_sha="abc123",
                branch="feature/test",
                base_branch="main",
                pr_url=None,
                pr_number=None,
                pr_reused=False,
                message="No changes",
            )
        )
        sync_cls.return_value = sync_service

        result = await workflow_store_service.publish_workflow_dsl(
            workflow_id=workflow_id,
            dsl=sample_dsl,
            params=WorkflowDslPublish(branch="feature/test", create_pr=False),
            workflow=cast(Workflow, workflow),
        )

    assert result.status == "no_op"
    sync_service.export_workflow_publish_result.assert_awaited_once()
    call = sync_service.export_workflow_publish_result.call_args.kwargs
    assert call["workflow"] is workflow
    assert call["dsl"] is sample_dsl
    assert call["options"].branch == "feature/test"
    assert call["options"].create_pr is False


@pytest.mark.anyio
async def test_publish_workflow_legacy_mode_uses_temp_branch_and_pr(
    workflow_store_service: WorkflowStoreService,
    sample_dsl: DSLInput,
) -> None:
    workflow_id = WorkflowUUID.new_uuid4()
    workflow = _workflow_fixture(
        workflow_id,
        case_trigger=SimpleNamespace(
            status="offline",
            event_types=[],
            tag_filters=[],
        ),
    )

    with patch("tracecat.workflow.store.service.WorkspaceGitSyncService") as sync_cls:
        sync_service = AsyncMock()
        sync_service.export_workflow_publish_result.return_value = (
            WorkflowDslPublishResult(
                status="no_op",
                commit_sha="abc123",
                branch="tracecat-sync-20260605-000000",
                base_branch="main",
                pr_url=None,
                pr_number=None,
                pr_reused=False,
                message="No changes",
            )
        )
        sync_cls.return_value = sync_service

        await workflow_store_service.publish_workflow_dsl(
            workflow_id=workflow_id,
            dsl=sample_dsl,
            params=WorkflowDslPublish(branch=None, create_pr=False),
            workflow=cast(Workflow, workflow),
        )

    call = sync_service.export_workflow_publish_result.call_args.kwargs
    assert call["options"].branch.startswith("tracecat-sync-")
    assert call["options"].create_pr is True
