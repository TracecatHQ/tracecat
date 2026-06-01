import json

from tracecat.dsl.common import DSLInput
from tracecat.expressions.expectations import ExpectedField
from tracecat.workflow.management.management import WorkflowsManagementService


def test_workflow_fields_from_dsl_serializes_expected_fields() -> None:
    dsl = DSLInput(
        **{
            "title": "Workflow with expects",
            "description": "Ensures expects can be written to JSONB columns",
            "entrypoint": {
                "ref": "start",
                "expects": {
                    "report_url": ExpectedField(type="str", optional=True),
                    "report_text": ExpectedField(type="str", optional=True),
                },
            },
            "actions": [
                {
                    "ref": "start",
                    "action": "core.transform.reshape",
                    "args": {"value": "ok"},
                }
            ],
        }
    )

    fields = WorkflowsManagementService._workflow_fields_from_dsl(dsl)

    assert fields["expects"] == {
        "report_url": {
            "type": "str",
            "description": None,
            "enum": None,
            "optional": True,
        },
        "report_text": {
            "type": "str",
            "description": None,
            "enum": None,
            "optional": True,
        },
    }
    json.dumps(fields["expects"])
