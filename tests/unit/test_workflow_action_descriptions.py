"""Action descriptions survive draft reads, exports, and definition round trips."""

import uuid
from typing import Literal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.db.models import Workflow
from tracecat.dsl.common import DSLInput
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.workflow.management.management import WorkflowsManagementService
from tracecat.workflow.management.router import export_workflow, get_workflow_draft
from tracecat.workflow.management.schemas import ExternalWorkflowDefinition


@pytest.mark.anyio
@pytest.mark.parametrize("format", ["json", "yaml"])
async def test_action_descriptions_survive_definition_round_trip(
    format: Literal["json", "yaml"],
) -> None:
    """Exercise real conversion and route serialization with database I/O mocked."""
    role = Role(
        type="service",
        service_id="tracecat-api",
        organization_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        scopes=frozenset({"*"}),
    )
    session = MagicMock(spec=AsyncSession)
    service = WorkflowsManagementService(session, role=role)
    workflow_id = WorkflowUUID.new_uuid4()
    description = "Collect input records"
    definition = DSLInput.model_validate(
        {
            "title": "Description preservation",
            "description": "Synthetic workflow for description preservation",
            "entrypoint": {"expects": {}},
            "actions": [
                {
                    "ref": "reshape_value",
                    "description": description,
                    "action": "core.transform.reshape",
                    "args": {"value": 3},
                }
            ],
        }
    )
    actions = await service.create_actions_from_dsl(definition, workflow_id)
    assert actions[0].description == description
    workflow = Workflow(
        id=workflow_id,
        workspace_id=role.workspace_id,
        title=definition.title,
        description=definition.description,
        status="offline",
        expects={},
        config={},
        actions=actions,
        schedules=[],
        case_trigger=None,
    )

    revisions: set[str] = set()
    with (
        patch.object(
            WorkflowsManagementService,
            "get_workflow",
            AsyncMock(return_value=workflow),
        ),
        patch(
            "tracecat.workflow.management.router.get_setting",
            AsyncMock(return_value=True),
        ),
    ):
        for current in [description, "Updated notes:\nKeep café records.", ""]:
            actions[0].description = current
            draft = await get_workflow_draft(
                role=role, session=session, workflow_id=workflow_id
            )
            assert draft.document.definition.actions[0].description == current
            revisions.add(draft.draft_revision)

            response = await export_workflow(
                role=role,
                session=session,
                workflow_id=workflow_id,
                format=format,
                version=None,
                draft=True,
            )
            exported = ExternalWorkflowDefinition.model_validate(
                yaml.safe_load(bytes(response.body))
            )
            assert exported.definition.actions[0].description == current
            restored_actions = await service.create_actions_from_dsl(
                exported.definition, workflow_id
            )
            assert restored_actions[0].description == current

            # Publishing stores the rebuilt definition with exclude_unset=True.
            built = await service.build_dsl_from_workflow(workflow)
            content = built.model_dump(exclude_unset=True)
            assert content["actions"][0]["description"] == current

    assert len(revisions) == 3
