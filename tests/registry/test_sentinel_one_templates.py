from collections.abc import Callable, Mapping
from pathlib import Path
from typing import cast

import pytest

from tracecat.registry.actions.schemas import TemplateAction

TEMPLATE_ROOT = Path(
    "packages/tracecat-registry/tracecat_registry/templates/tools/sentinel_one"
)


def load_template(filename: str) -> TemplateAction:
    return TemplateAction.from_yaml(TEMPLATE_ROOT / filename)


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
    template = load_template(filename)
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


@pytest.mark.parametrize("filename", ["powerquery.yml", "submit_powerquery.yml"])
def test_powerquery_submission_contract(filename: str) -> None:
    definition = load_template(filename).definition

    assert definition.expects["query_priority"].default == "LOW"
    assert definition.expects["result_type"].default == "TABLE"
    assert definition.expects["frequency"].default == "LOW"
    assert definition.expects["timeout_seconds"].default == 60

    build_step = definition.steps[0]
    namespace: dict[str, object] = {}
    exec(build_step.args["script"], namespace)
    build_payload = cast(Callable[..., object], namespace["main"])
    result = build_payload(
        start_time="24h",
        end_time="0s",
        query_priority="LOW",
        tenant=True,
        account_ids=None,
        query_origin=None,
        query="| group count() by event.type",
        result_type="TABLE",
        frequency="LOW",
    )

    assert isinstance(result, Mapping)
    payload = result["payload"]
    assert isinstance(payload, Mapping)
    assert payload["queryType"] == "PQ"
    assert payload["queryPriority"] == "LOW"
    assert payload["pq"] == {
        "query": "| group count() by event.type",
        "resultType": "TABLE",
        "frequency": "LOW",
    }
    assert payload["tenant"] is True
    assert "accountIds" not in payload

    submit_step = definition.steps[1]
    assert submit_step.action == "core.http_request"
    assert submit_step.args["method"] == "POST"
    assert submit_step.args["url"].endswith("/sdl/v2/api/queries")
    assert submit_step.args["timeout"] == "${{ inputs.timeout_seconds }}"


def test_powerquery_poll_contract() -> None:
    definition = load_template("powerquery.yml").definition

    assert definition.expects["poll_interval"].default == 1
    assert definition.expects["poll_max_attempts"].default == 300

    poll_step = definition.steps[2]
    assert poll_step.action == "core.http_poll"
    assert poll_step.args["method"] == "GET"
    assert poll_step.args["params"] == {"lastStepSeen": 0}
    assert poll_step.args["headers"]["X-Dataset-Query-Forward-Tag"] == (
        '${{ steps.submit_query.result.headers["x-dataset-query-forward-tag"] }}'
    )

    delete_step = definition.steps[3]
    assert delete_step.action == "core.http_request"
    assert delete_step.args["method"] == "DELETE"
    assert delete_step.args["url"].endswith(
        "/sdl/v2/api/queries/${{ steps.submit_query.result.data.id }}"
    )
    assert delete_step.args["headers"]["X-Dataset-Query-Forward-Tag"] == (
        '${{ steps.submit_query.result.headers["x-dataset-query-forward-tag"] }}'
    )


@pytest.mark.parametrize(
    ("filename", "method", "endpoint"),
    [
        (
            "get_powerquery_results.yml",
            "GET",
            "/sdl/v2/api/queries/${{ inputs.query_id }}",
        ),
        (
            "delete_powerquery.yml",
            "DELETE",
            "/sdl/v2/api/queries/${{ inputs.query_id }}",
        ),
    ],
)
def test_powerquery_followup_contract(
    filename: str, method: str, endpoint: str
) -> None:
    definition = load_template(filename).definition
    step = definition.steps[0]

    assert definition.expects["timeout_seconds"].default == 60
    assert step.args["method"] == method
    assert step.args["url"] == (
        "${{ inputs.base_url || VARS.sentinel_one.base_url }}" + endpoint
    )
    assert step.args["headers"]["Authorization"].startswith("Bearer ")
    assert step.args["headers"]["X-Dataset-Query-Forward-Tag"] == (
        "${{ inputs.forward_tag }}"
    )


def test_purple_ai_graphql_contract() -> None:
    definition = load_template("purple_ai.yml").definition

    assert definition.expects["timeout_seconds"].default == 120

    build_step = definition.steps[0]
    namespace: dict[str, object] = {}
    exec(build_step.args["script"], namespace)
    build_query = cast(Callable[..., object], namespace["main"])
    query = build_query(
        base_url="https://console.example.test",
        console_id=None,
        tenant_id="tenant-id",
        account_id="account-id",
        site_id=None,
        version="test-version",
        start_time=1,
        end_time=2,
    )

    assert isinstance(query, str)
    assert "query SimpleTestQuery($input: String!)" in query
    assert "purpleLaunchQuery" in query
    assert query.count("tenantDetails:") == 2
    assert query.count("userTime:") == 2
    assert "displayedTimeRange: { start: 1, end: 2 }" in query

    request_step = definition.steps[1]
    assert request_step.args["url"].endswith("/web/api/v2.1/graphql")
    assert request_step.args["headers"]["Authorization"].startswith("ApiToken ")
    assert request_step.args["payload"] == {
        "query": "${{ steps.build_query.result }}",
        "variables": {"input": "${{ inputs.question }}"},
    }
    assert request_step.args["timeout"] == "${{ inputs.timeout_seconds }}"


def test_graphql_passthrough_contract() -> None:
    definition = load_template("graphql.yml").definition
    step = definition.steps[0]

    assert definition.expects["endpoint"].default == "/web/api/v2.1/graphql"
    assert definition.expects["auth_scheme"].default == "ApiToken"
    assert definition.expects["timeout_seconds"].default == 30
    assert step.args["method"] == "POST"
    assert step.args["payload"] == {
        "query": "${{ inputs.query }}",
        "variables": "${{ inputs.variables }}",
    }


def test_inventory_search_contract() -> None:
    definition = load_template("list_inventory.yml").definition
    step = definition.steps[0]

    assert step.args["url"].endswith("/web/api/v2.1/xdr/assets")
    assert step.args["method"] == "POST"
    assert step.args["headers"]["Authorization"].startswith("Bearer ")
    assert step.args["payload"] == {
        "filter": '${{ FN.merge([inputs.filters, {"limit": inputs.limit, "skip": inputs.skip}]) }}'
    }
    assert step.args["timeout"] == 30