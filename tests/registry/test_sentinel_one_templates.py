from pathlib import Path

import pytest

from tracecat.registry.actions.schemas import TemplateAction

TEMPLATE_ROOT = Path(
    "packages/tracecat-registry/tracecat_registry/templates/tools/sentinel_one"
)


@pytest.mark.parametrize(
    ("filename", "action_name", "endpoint", "data_key", "data_input"),
    [
        (
            "update_alert_analyst_verdict.yml",
            "tools.sentinel_one.update_alert_analyst_verdict",
            "/web/api/v2.1/cloud-detection/alerts/analyst-verdict",
            "analystVerdict",
            "${{ inputs.analyst_verdict }}",
        ),
        (
            "update_alert_incident_status.yml",
            "tools.sentinel_one.update_alert_incident_status",
            "/web/api/v2.1/cloud-detection/alerts/incident",
            "incidentStatus",
            "${{ inputs.incident_status }}",
        ),
    ],
)
def test_alert_lifecycle_template_contract(
    filename: str,
    action_name: str,
    endpoint: str,
    data_key: str,
    data_input: str,
) -> None:
    template = TemplateAction.from_yaml(TEMPLATE_ROOT / filename)
    definition = template.definition

    assert definition.action == action_name
    assert definition.expects["alert_ids"].type == "list[str]"
    assert definition.expects["base_url"].default is None

    step = definition.steps[0]
    assert step.action == "core.http_request"
    assert step.args["method"] == "POST"
    assert step.args["url"] == (
        "${{ inputs.base_url || VARS.sentinel_one.base_url }}" + endpoint
    )
    assert step.args["payload"] == {
        "filter": {"ids": "${{ inputs.alert_ids }}"},
        "data": {data_key: data_input},
    }
