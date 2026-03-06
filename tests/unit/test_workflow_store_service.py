"""Unit tests for WorkflowStoreService bulk push behavior."""

from __future__ import annotations

import uuid
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.workflow.store.schemas import (
    WorkflowBulkPushExclusionReason,
    WorkflowBulkPushPreviewRequest,
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
        scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-api"],
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
