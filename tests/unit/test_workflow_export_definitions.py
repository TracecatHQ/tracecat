"""Workspace-only draft fallback and safe named push diagnostics."""

import uuid
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.db.models import Workflow
from tracecat.dsl.common import DSLInput
from tracecat.exceptions import TracecatValidationError
from tracecat.workspace_sync.exceptions import WorkflowExportValidationError
from tracecat.workspace_sync.schemas import WorkflowExportDiagnostic
from tracecat.workspace_sync.service import WorkspaceSyncService


def _dsl(title: str = "Saved workflow") -> DSLInput:
    return DSLInput.model_validate(
        {
            "title": title,
            "description": "Synthetic export fixture",
            "entrypoint": {},
            "actions": [
                {
                    "ref": "start",
                    "action": "core.transform.passthrough",
                    "args": {"value": "example"},
                }
            ],
        }
    )


@pytest.mark.anyio
@pytest.mark.parametrize("saved", [False, True])
async def test_resolve_saved_or_export_only_without_publishing(
    svc_role: Role,
    saved: bool,
) -> None:
    session = AsyncMock(spec=AsyncSession)
    workflow = cast(
        Workflow,
        SimpleNamespace(id=uuid.uuid4(), title="Current edits", actions=[object()]),
    )
    saved_dsl = _dsl()
    built_dsl = _dsl("Current edits")
    with (
        patch(
            "tracecat.workspace_sync.service.WorkflowDefinitionsService"
        ) as definitions,
        patch(
            "tracecat.workspace_sync.service.WorkflowsManagementService"
        ) as management,
    ):
        definitions.return_value.get_definition_by_workflow_id = AsyncMock(
            return_value=SimpleNamespace(content=saved_dsl.model_dump())
            if saved
            else None
        )
        management.return_value.build_dsl_from_workflow = AsyncMock(
            return_value=built_dsl
        )
        result = await WorkspaceSyncService(
            session=session, role=svc_role
        )._get_workflow_dsl(workflow)
        assert result.title == ("Saved workflow" if saved else "Current edits")
        if saved:
            management.return_value.build_dsl_from_workflow.assert_not_called()
            session.refresh.assert_not_awaited()
        else:
            management.return_value.build_dsl_from_workflow.assert_awaited_once_with(
                workflow
            )
        definitions.return_value.create_workflow_definition.assert_not_called()
        session.commit.assert_not_awaited()
        session.add.assert_not_called()


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["empty", "invalid_saved", "invalid_build"])
async def test_resolve_errors_are_named_and_do_not_include_raw_inputs(
    svc_role: Role,
    failure: str,
) -> None:
    session = AsyncMock(spec=AsyncSession)
    session.scalar.return_value = None
    workflow = cast(
        Workflow,
        SimpleNamespace(
            id=uuid.uuid4(),
            title="Example review",
            actions=[] if failure == "empty" else [object()],
        ),
    )
    with (
        patch(
            "tracecat.workspace_sync.service.WorkflowDefinitionsService"
        ) as definitions,
        patch(
            "tracecat.workspace_sync.service.WorkflowsManagementService"
        ) as management,
    ):
        definitions.return_value.get_definition_by_workflow_id = AsyncMock(
            return_value=SimpleNamespace(
                content={"title": "secret-value", "actions": "secret-value"}
            )
            if failure == "invalid_saved"
            else None
        )
        management.return_value.build_dsl_from_workflow = AsyncMock(
            side_effect=TracecatValidationError("secret-value")
        )
        with pytest.raises(WorkflowExportValidationError) as raised:
            await WorkspaceSyncService(
                session=session, role=svc_role
            )._get_workflow_dsl(workflow)
    diagnostic = raised.value.diagnostics[0]
    assert diagnostic.workflow_id == workflow.id
    assert diagnostic.workflow_title == "Example review"
    assert diagnostic.source_id is None
    assert diagnostic.source_path is None
    assert diagnostic.code == (
        "no_actions" if failure == "empty" else "invalid_definition"
    )
    assert "secret-value" not in str(raised.value)
    assert raised.value.__context__ is None
    session.commit.assert_not_awaited()
    session.add.assert_not_called()


def test_export_errors_have_deterministic_bounded_diagnostics() -> None:
    diagnostics = [
        WorkflowExportDiagnostic(
            workflow_id=uuid.UUID(int=index),
            workflow_title="Duplicate title",
            stage="definition",
            code="no_actions",
            message="Add an action before pushing.",
        )
        for index in reversed(range(55))
    ]
    error = WorkflowExportValidationError(diagnostics)
    assert len(error.diagnostics) == 50
    assert error.omitted_count == 5
    assert [item.workflow_id.int for item in error.diagnostics] == list(range(50))
