from typing import Any

import pytest
from sqlalchemy import select

from tracecat.auth.types import Role
from tracecat.db.models import CaseTag, CaseTrigger
from tracecat.dsl.common import DSLInput
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.workflow.case_triggers.schemas import CaseTriggerConfig
from tracecat.workflow.management.management import WorkflowsManagementService
from tracecat.workflow.management.schemas import ExternalWorkflowDefinition


@pytest.mark.parametrize(
    "id",
    [
        pytest.param(WorkflowUUID.new_uuid4().to_legacy(), id="legacy"),
        pytest.param(WorkflowUUID.new_uuid4(), id="workflow_uuid"),
        pytest.param(str(WorkflowUUID.new_uuid4()), id="str"),
        pytest.param(WorkflowUUID.new_uuid4().short(), id="short"),
    ],
)
@pytest.mark.anyio
async def test_workflow_can_import(test_role: Role, id: Any):
    dsl = ExternalWorkflowDefinition(
        workflow_id=id,
        definition=DSLInput(
            **{
                "title": "multiple_entrypoints",
                "description": "Test that workflow can have multiple entrypoints",
                "entrypoint": {"expects": {}, "ref": None},
                "actions": [
                    {
                        "ref": "entrypoint_1",
                        "action": "core.transform.reshape",
                        "args": {"value": "ENTRYPOINT_1"},
                    },
                    {
                        "ref": "entrypoint_2",
                        "action": "core.transform.reshape",
                        "args": {"value": "ENTRYPOINT_2"},
                    },
                    {
                        "ref": "entrypoint_3",
                        "action": "core.transform.reshape",
                        "args": {"value": "ENTRYPOINT_3"},
                    },
                    {
                        "ref": "join",
                        "action": "core.transform.reshape",
                        "args": {
                            "value": {
                                "first": "${{ ACTIONS.entrypoint_1.result }}",
                                "second": "${{ ACTIONS.entrypoint_2.result }}",
                                "third": "${{ ACTIONS.entrypoint_3.result }}",
                            }
                        },
                        "depends_on": ["entrypoint_1", "entrypoint_2", "entrypoint_3"],
                        "join_strategy": "all",
                    },
                ],
                "returns": "${{ ACTIONS.join.result }}",
            }
        ),
    )

    # Import it into the database
    async with WorkflowsManagementService.with_session(test_role) as service:
        import_data = dsl.model_dump(mode="json")

        workflow = await service.create_workflow_from_external_definition(import_data)

        assert workflow.actions and len(workflow.actions) == 4


@pytest.mark.anyio
async def test_workflow_import_case_trigger_creates_tags(test_role: Role):
    dsl = ExternalWorkflowDefinition(
        definition=DSLInput(
            **{
                "title": "case_trigger_import",
                "description": "Workflow import with case trigger",
                "entrypoint": {"expects": {}, "ref": None},
                "actions": [
                    {
                        "ref": "entrypoint_1",
                        "action": "core.transform.reshape",
                        "args": {"value": "ENTRYPOINT_1"},
                    }
                ],
                "returns": "${{ ACTIONS.entrypoint_1.result }}",
            }
        ),
        case_trigger=CaseTriggerConfig(
            status="offline",
            event_types=[],
            tag_filters=["phishing"],
        ),
    )

    async with WorkflowsManagementService.with_session(test_role) as service:
        import_data = dsl.model_dump(mode="json")
        workflow = await service.create_workflow_from_external_definition(import_data)

        stmt = select(CaseTrigger).where(CaseTrigger.workflow_id == workflow.id)
        result = await service.session.execute(stmt)
        case_trigger = result.scalar_one()
        assert case_trigger.tag_filters == ["phishing"]

        tag_stmt = select(CaseTag).where(
            CaseTag.workspace_id == workflow.workspace_id, CaseTag.ref == "phishing"
        )
        tag_result = await service.session.execute(tag_stmt)
        assert tag_result.scalar_one().ref == "phishing"
