"""Unit tests for WorkflowStoreService bulk push behavior."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

from tracecat.auth.types import Role
from tracecat.exceptions import TracecatValidationError
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.workflow.store.schemas import (
    WorkflowBulkPushExclusionReason,
    WorkflowBulkPushPreviewRequest,
    WorkflowBulkPushWorkflowSummary,
)
from tracecat.workflow.store.service import WorkflowStoreService


def _sample_dsl_content() -> dict[str, object]:
    return {
        "title": "Test workflow",
        "description": "A test workflow",
        "entrypoint": {"ref": "start", "expects": {}},
        "actions": [
            {
                "ref": "start",
                "action": "core.transform.passthrough",
                "args": {"value": "test"},
            }
        ],
    }


@pytest.fixture
def workflow_store_service() -> WorkflowStoreService:
    workspace_id = uuid.UUID("550e8400-e29b-41d4-a716-446655440000")
    organization_id = uuid.UUID("550e8400-e29b-41d4-a716-446655440001")
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=workspace_id,
        organization_id=organization_id,
    )
    return WorkflowStoreService(session=AsyncMock(), role=role)


@pytest.mark.anyio
async def test_resolve_bulk_push_selection_excludes_invalid_configuration(
    workflow_store_service: WorkflowStoreService,
) -> None:
    """Bulk preview should exclude workflows that fail remote export validation."""
    workflow_id = WorkflowUUID.new(uuid.uuid4())
    workflow = SimpleNamespace(
        title="Broken workflow",
        alias="broken-workflow",
        webhook=None,
        folder=None,
        tags=[],
        schedules=[],
        case_trigger=None,
    )
    definition = SimpleNamespace(
        content=_sample_dsl_content(),
        version=3,
        created_at=datetime(2026, 3, 6, 12, 0, 0),
    )

    with (
        patch.object(
            workflow_store_service,
            "_get_workflows_by_short_id",
            AsyncMock(return_value={workflow_id.short(): workflow}),
        ),
        patch.object(
            workflow_store_service,
            "_get_latest_definitions_by_short_id",
            AsyncMock(return_value={workflow_id.short(): definition}),
        ),
    ):
        resolution = await workflow_store_service._resolve_bulk_push_selection(
            workflow_ids=[workflow_id],
            folder_paths=[],
        )

    assert resolution.prepared_items == []
    assert resolution.eligible_workflows == []
    assert resolution.resolved_workflow_ids == [workflow_id.short()]
    assert len(resolution.excluded_workflows) == 1
    assert (
        resolution.excluded_workflows[0].reason
        == WorkflowBulkPushExclusionReason.INVALID_CONFIGURATION
    )
    assert workflow_id.short() in resolution.excluded_workflows[0].message


@pytest.mark.anyio
async def test_resolve_bulk_push_selection_excludes_invalid_stored_dsl(
    workflow_store_service: WorkflowStoreService,
) -> None:
    """Bulk preview should exclude workflows with malformed stored DSL content."""
    workflow_id = WorkflowUUID.new(uuid.uuid4())
    workflow = SimpleNamespace(
        title="Corrupt workflow",
        alias="corrupt-workflow",
        webhook=None,
        folder=None,
        tags=[],
        schedules=[],
        case_trigger=None,
    )
    definition = SimpleNamespace(
        content={"title": "Corrupt workflow"},
        version=7,
        created_at=datetime(2026, 3, 6, 12, 0, 0),
    )

    with (
        patch.object(
            workflow_store_service,
            "_get_workflows_by_short_id",
            AsyncMock(return_value={workflow_id.short(): workflow}),
        ),
        patch.object(
            workflow_store_service,
            "_get_latest_definitions_by_short_id",
            AsyncMock(return_value={workflow_id.short(): definition}),
        ),
    ):
        resolution = await workflow_store_service._resolve_bulk_push_selection(
            workflow_ids=[workflow_id],
            folder_paths=[],
        )

    assert resolution.prepared_items == []
    assert resolution.eligible_workflows == []
    assert resolution.resolved_workflow_ids == [workflow_id.short()]
    assert len(resolution.excluded_workflows) == 1
    assert (
        resolution.excluded_workflows[0].reason
        == WorkflowBulkPushExclusionReason.INVALID_CONFIGURATION
    )
    assert "Field required" in resolution.excluded_workflows[0].message


@pytest.mark.anyio
async def test_list_workflow_ids_in_selected_folders_autoescapes_like_wildcards(
    workflow_store_service: WorkflowStoreService,
) -> None:
    """Folder selection SQL should escape wildcard characters in user paths."""
    result = Mock()
    result.scalars.return_value.all.return_value = []
    with patch.object(
        workflow_store_service.session,
        "execute",
        AsyncMock(return_value=result),
    ) as mock_execute:
        await workflow_store_service._list_workflow_ids_in_selected_folders(
            ["/sec_ur/%ops"]
        )

    statement = mock_execute.call_args.args[0]
    compiled = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "ESCAPE '/'" in compiled
    assert "LIKE" in compiled


def test_bulk_push_preview_request_rejects_non_list_folder_paths() -> None:
    """Schema should reject malformed folder_paths values before normalization."""
    with pytest.raises(ValidationError, match="folder_paths must be a list of strings"):
        WorkflowBulkPushPreviewRequest.model_validate(
            {
                "workflow_ids": ["wf_123abc"],
                "folder_paths": "/security",
            }
        )


def test_to_workflow_uuids_rejects_invalid_short_ids(
    workflow_store_service: WorkflowStoreService,
) -> None:
    """Malformed short IDs should be translated into a validation error."""
    with patch(
        "tracecat.workflow.store.service.WorkflowUUID.new",
        side_effect=ValueError("bad workflow id"),
    ):
        with pytest.raises(
            TracecatValidationError,
            match="workflow_ids contains an invalid workflow ID",
        ):
            workflow_store_service._to_workflow_uuids(["wf_badbadbad"])


def test_build_bulk_push_defaults_adds_entropy_to_branch_names(
    workflow_store_service: WorkflowStoreService,
) -> None:
    """Default bulk branch names should not collide within the same timestamp."""
    eligible_workflows = [
        WorkflowBulkPushWorkflowSummary(
            workflow_id="wf_123abc",
            title="Workflow 1",
            alias="workflow-1",
            folder_path="/security",
            latest_definition_version=3,
            latest_definition_created_at=datetime(2026, 3, 6, 12, 0, 0),
        )
    ]

    with (
        patch("tracecat.workflow.store.service.datetime") as mock_datetime,
        patch("tracecat.workflow.store.service.uuid4") as mock_uuid4,
    ):
        mock_datetime.now.return_value.strftime.return_value = "20260306-120000-123456"
        mock_uuid4.side_effect = [
            SimpleNamespace(hex="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),
            SimpleNamespace(hex="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"),
        ]

        first_branch, *_ = workflow_store_service._build_bulk_push_defaults(
            workspace_name="Test workspace",
            eligible_workflows=eligible_workflows,
        )
        second_branch, *_ = workflow_store_service._build_bulk_push_defaults(
            workspace_name="Test workspace",
            eligible_workflows=eligible_workflows,
        )

    assert first_branch == "tracecat/bulk-push-20260306-120000-123456-aaaaaaaa"
    assert second_branch == "tracecat/bulk-push-20260306-120000-123456-bbbbbbbb"
    assert first_branch != second_branch
    assert re.match(r"^tracecat/bulk-push-\d{8}-\d{6}-\d{6}-[0-9a-f]{8}$", first_branch)
