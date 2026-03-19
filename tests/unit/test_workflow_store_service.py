"""Tests for WorkflowStoreService publishing behavior."""

import uuid
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock, patch

import pytest

from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.cases.enums import CaseEventType
from tracecat.db.models import Workflow
from tracecat.dsl.common import DSLEntrypoint, DSLInput
from tracecat.dsl.schemas import ActionStatement
from tracecat.exceptions import TracecatValidationError
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.sync import PushStatus
from tracecat.workflow.store.schemas import WorkflowDslPublish
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
        webhook=SimpleNamespace(methods=["POST"], status="online"),
        case_trigger=case_trigger,
        git_sync_branch=None,
    )


@pytest.mark.anyio
async def test_publish_workflow_omits_inert_case_trigger(
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
            event_filters={},
        ),
    )

    with (
        patch("tracecat.workflow.store.service.WorkspaceService") as workspace_cls,
        patch("tracecat.workflow.store.service.WorkflowSyncService") as sync_cls,
    ):
        workspace_service = AsyncMock()
        workspace_service.get_workspace.return_value = SimpleNamespace(
            settings={"git_repo_url": "git+ssh://git@github.com/test-org/test-repo.git"}
        )
        workspace_cls.return_value = workspace_service

        sync_service = AsyncMock()
        sync_service.push.return_value = SimpleNamespace(
            status=PushStatus.NO_OP,
            sha="abc123",
            ref="feature/test",
            base_ref="main",
            pr_url=None,
            pr_number=None,
            pr_reused=False,
            message="No changes",
        )
        sync_cls.return_value = sync_service

        await workflow_store_service.publish_workflow_dsl(
            workflow_id=workflow_id,
            dsl=sample_dsl,
            params=WorkflowDslPublish(branch="feature/test", create_pr=False),
            workflow=cast(Workflow, workflow),
        )

    push_obj = sync_service.push.call_args.kwargs["objects"][0]
    assert push_obj.data.case_trigger is None


@pytest.mark.anyio
async def test_publish_workflow_includes_configured_case_trigger(
    workflow_store_service: WorkflowStoreService,
    sample_dsl: DSLInput,
) -> None:
    workflow_id = WorkflowUUID.new_uuid4()
    workflow = _workflow_fixture(
        workflow_id,
        case_trigger=SimpleNamespace(
            status="online",
            event_types=[
                CaseEventType.CASE_CREATED.value,
                CaseEventType.STATUS_CHANGED.value,
            ],
            tag_filters=["phishing"],
            event_filters={"status_changed": ["resolved"]},
        ),
    )

    with (
        patch("tracecat.workflow.store.service.WorkspaceService") as workspace_cls,
        patch("tracecat.workflow.store.service.WorkflowSyncService") as sync_cls,
    ):
        workspace_service = AsyncMock()
        workspace_service.get_workspace.return_value = SimpleNamespace(
            settings={"git_repo_url": "git+ssh://git@github.com/test-org/test-repo.git"}
        )
        workspace_cls.return_value = workspace_service

        sync_service = AsyncMock()
        sync_service.push.return_value = SimpleNamespace(
            status=PushStatus.NO_OP,
            sha="abc123",
            ref="feature/test",
            base_ref="main",
            pr_url=None,
            pr_number=None,
            pr_reused=False,
            message="No changes",
        )
        sync_cls.return_value = sync_service

        await workflow_store_service.publish_workflow_dsl(
            workflow_id=workflow_id,
            dsl=sample_dsl,
            params=WorkflowDslPublish(branch="feature/test", create_pr=False),
            workflow=cast(Workflow, workflow),
        )

    push_obj = sync_service.push.call_args.kwargs["objects"][0]
    assert push_obj.data.case_trigger is not None
    assert push_obj.data.case_trigger.status == "online"
    assert push_obj.data.case_trigger.event_types == [
        CaseEventType.CASE_CREATED,
        CaseEventType.STATUS_CHANGED,
    ]
    assert push_obj.data.case_trigger.tag_filters == ["phishing"]
    assert push_obj.data.case_trigger.event_filters.status_changed == ["resolved"]


@pytest.mark.anyio
async def test_publish_workflow_wraps_invalid_stored_case_trigger(
    workflow_store_service: WorkflowStoreService,
    sample_dsl: DSLInput,
) -> None:
    workflow_id = WorkflowUUID.new_uuid4()
    workflow = _workflow_fixture(
        workflow_id,
        case_trigger=SimpleNamespace(
            status="online",
            event_types=[CaseEventType.SEVERITY_CHANGED.value],
            tag_filters=[],
            event_filters={"status_changed": ["resolved"]},
        ),
    )

    with (
        patch("tracecat.workflow.store.service.WorkspaceService") as workspace_cls,
        patch("tracecat.workflow.store.service.WorkflowSyncService") as sync_cls,
    ):
        workspace_service = AsyncMock()
        workspace_service.get_workspace.return_value = SimpleNamespace(
            settings={"git_repo_url": "git+ssh://git@github.com/test-org/test-repo.git"}
        )
        workspace_cls.return_value = workspace_service

        sync_service = AsyncMock()
        sync_cls.return_value = sync_service

        with pytest.raises(TracecatValidationError):
            await workflow_store_service.publish_workflow_dsl(
                workflow_id=workflow_id,
                dsl=sample_dsl,
                params=WorkflowDslPublish(branch="feature/test", create_pr=False),
                workflow=cast(Workflow, workflow),
            )

    sync_service.push.assert_not_called()
