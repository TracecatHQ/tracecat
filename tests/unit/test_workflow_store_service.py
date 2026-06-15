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
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.workflow.store.schemas import WorkflowDslPublish, WorkflowDslPublishResult
from tracecat.workflow.store.service import WorkflowStoreService
from tracecat.workspace_sync.workflow import workflow_spec_from_orm


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
async def test_publish_workflow_uses_workspace_sync_service(
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

    with patch("tracecat.workflow.store.service.WorkspaceSyncService") as sync_cls:
        sync_service = AsyncMock()
        sync_service.export_workflow_publish_result.return_value = (
            WorkflowDslPublishResult(
                status="no_op",
                commit_sha=None,
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

    assert result.branch == "feature/test"
    call_kwargs = sync_service.export_workflow_publish_result.call_args.kwargs
    assert call_kwargs["workflow"] is workflow
    assert call_kwargs["dsl"] is sample_dsl
    assert call_kwargs["options"].branch == "feature/test"
    assert call_kwargs["options"].create_pr is False


def test_workflow_spec_omits_inert_case_trigger_and_schedules_by_default(
    sample_dsl: DSLInput,
) -> None:
    workflow = _workflow_fixture(
        WorkflowUUID.new_uuid4(),
        case_trigger=SimpleNamespace(
            status="offline",
            event_types=[],
            tag_filters=[],
        ),
    )
    workflow.schedules = [
        SimpleNamespace(
            status="online",
            cron="* * * * *",
            every=None,
            offset=None,
            start_at=None,
            end_at=None,
            timeout=10.0,
        )
    ]

    spec = workflow_spec_from_orm(
        cast(Workflow, workflow),
        dsl=sample_dsl,
        source_id="test-workflow",
    )

    assert spec.case_trigger is None
    assert spec.schedules is None
    assert spec.webhook is not None
    assert spec.webhook.include_headers is True


def test_workflow_spec_can_include_schedules(sample_dsl: DSLInput) -> None:
    workflow = _workflow_fixture(
        WorkflowUUID.new_uuid4(),
        case_trigger=None,
    )
    workflow.schedules = [
        SimpleNamespace(
            status="online",
            cron="* * * * *",
            every=None,
            offset=None,
            start_at=None,
            end_at=None,
            timeout=10.0,
        )
    ]

    spec = workflow_spec_from_orm(
        cast(Workflow, workflow),
        dsl=sample_dsl,
        source_id="test-workflow",
        include_schedules=True,
    )

    assert spec.schedules is not None
    assert len(spec.schedules) == 1
    assert spec.schedules[0].cron == "* * * * *"


def test_workflow_spec_includes_configured_case_trigger(
    sample_dsl: DSLInput,
) -> None:
    workflow = _workflow_fixture(
        WorkflowUUID.new_uuid4(),
        case_trigger=SimpleNamespace(
            status="online",
            event_types=[CaseEventType.CASE_CREATED.value],
            tag_filters=["phishing"],
        ),
    )

    spec = workflow_spec_from_orm(
        cast(Workflow, workflow),
        dsl=sample_dsl,
        source_id="test-workflow",
    )

    assert spec.case_trigger is not None
    assert spec.case_trigger.status == "online"
    assert spec.case_trigger.event_types == [CaseEventType.CASE_CREATED]
    assert spec.case_trigger.tag_filters == ["phishing"]


@pytest.mark.anyio
async def test_publish_workflow_legacy_branch_still_creates_pr(
    workflow_store_service: WorkflowStoreService,
    sample_dsl: DSLInput,
) -> None:
    workflow_id = WorkflowUUID.new_uuid4()
    workflow = _workflow_fixture(workflow_id, case_trigger=None)

    with patch("tracecat.workflow.store.service.WorkspaceSyncService") as sync_cls:
        sync_service = AsyncMock()
        sync_service.export_workflow_publish_result.return_value = (
            WorkflowDslPublishResult(
                status="committed",
                commit_sha="abc123",
                branch="tracecat-sync-test",
                base_branch="main",
                pr_url="https://github.com/test-org/test-repo/pull/1",
                pr_number=1,
                pr_reused=False,
                message="Committed workspace sync changes.",
            )
        )
        sync_cls.return_value = sync_service

        await workflow_store_service.publish_workflow_dsl(
            workflow_id=workflow_id,
            dsl=sample_dsl,
            params=WorkflowDslPublish(),
            workflow=cast(Workflow, workflow),
        )

    options = sync_service.export_workflow_publish_result.call_args.kwargs["options"]
    assert options.branch.startswith("tracecat-sync-")
    assert options.create_pr is True


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
            event_types=[CaseEventType.CASE_CREATED.value],
            tag_filters=["phishing"],
        ),
    )

    with patch("tracecat.workflow.store.service.WorkspaceSyncService") as sync_cls:
        sync_service = AsyncMock()
        sync_service.export_workflow_publish_result.return_value = (
            WorkflowDslPublishResult(
                status="no_op",
                commit_sha=None,
                branch="feature/test",
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
            params=WorkflowDslPublish(branch="feature/test", create_pr=False),
            workflow=cast(Workflow, workflow),
        )

    sync_service.export_workflow_publish_result.assert_awaited_once()
