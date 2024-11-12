import pytest

from tracecat.dsl.common import DSLInput
from tracecat.types.auth import Role
from tracecat.workflow.management.management import WorkflowsManagementService
from tracecat.workflow.management.models import ExternalWorkflowDefinition


@pytest.mark.asyncio
async def test_workflow_can_import(test_role: Role):
    dsl = ExternalWorkflowDefinition(
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
        )
    )

    # Import it into the database
    async with WorkflowsManagementService.with_session(test_role) as service:
        import_data = dsl.model_dump(mode="json")

        workflow = await service.create_workflow_from_external_definition(import_data)

        assert workflow.actions and len(workflow.actions) == 4
