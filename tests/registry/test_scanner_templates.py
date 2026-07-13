from pathlib import Path
from typing import Any

import pytest
from tracecat_registry import RegistrySecret

from tracecat.registry.actions.schemas import ActionStep, TemplateAction

TEMPLATE_ROOT = Path(
    "packages/tracecat-registry/tracecat_registry/templates/tools/scanner"
)

EXPECTED_ACTIONS = {
    "tools.scanner.cancel_query",
    "tools.scanner.create_detection_rule",
    "tools.scanner.create_event_sink",
    "tools.scanner.delete_detection_rule",
    "tools.scanner.delete_event_sink",
    "tools.scanner.get_detection_rule",
    "tools.scanner.get_event_sink",
    "tools.scanner.get_query_progress",
    "tools.scanner.list_detection_rules",
    "tools.scanner.list_event_sinks",
    "tools.scanner.run_detection_rule_yaml_tests",
    "tools.scanner.run_query",
    "tools.scanner.start_query",
    "tools.scanner.update_detection_rule",
    "tools.scanner.update_event_sink",
    "tools.scanner.validate_detection_rule_yaml",
}


def _load_template(filename: str) -> TemplateAction:
    return TemplateAction.from_yaml(TEMPLATE_ROOT / filename)


def _step(action: TemplateAction, ref: str) -> ActionStep:
    return next(step for step in action.definition.steps if step.ref == ref)


def _run_python_step(step: ActionStep, **inputs: Any) -> Any:
    namespace: dict[str, Any] = {}
    exec(step.args["script"], namespace)  # noqa: S102
    return namespace["main"](**inputs)


@pytest.fixture(scope="module")
def templates() -> list[TemplateAction]:
    return [
        TemplateAction.from_yaml(path) for path in sorted(TEMPLATE_ROOT.glob("*.yml"))
    ]


def test_scanner_template_inventory(templates: list[TemplateAction]) -> None:
    assert {template.definition.action for template in templates} == EXPECTED_ACTIONS


def test_scanner_templates_share_auth_and_base_url_contract(
    templates: list[TemplateAction],
) -> None:
    for template in templates:
        assert template.definition.secrets is not None
        secret = template.definition.secrets[0]
        assert isinstance(secret, RegistrySecret)
        assert secret.name == "scanner"
        assert secret.keys == ["SCANNER_API_KEY"]

        base_url = template.definition.expects["base_url"]
        assert base_url.type == "str | None"
        assert base_url.default is None

        http_step = next(
            step
            for step in template.definition.steps
            if step.action == "core.http_request"
        )
        assert http_step.args["url"].startswith(
            "${{ inputs.base_url || VARS.scanner.base_url }}"
        )
        assert http_step.args["headers"]["Authorization"] == (
            "Bearer ${{ SECRETS.scanner.SCANNER_API_KEY }}"
        )


def test_query_payload_omits_none_and_preserves_false() -> None:
    template = _load_template("run_query.yml")
    payload = _run_python_step(
        _step(template, "build_query_payload"),
        query="error | count",
        start_time="2026-07-13T00:00:00Z",
        end_time="2026-07-13T01:00:00Z",
        max_rows=None,
        max_bytes=None,
        scan_back_to_front=False,
    )

    assert payload == {
        "query": "error | count",
        "start_time": "2026-07-13T00:00:00Z",
        "end_time": "2026-07-13T01:00:00Z",
        "scan_back_to_front": False,
    }


def test_detection_rule_pagination_uses_documented_parameter_names() -> None:
    template = _load_template("list_detection_rules.yml")
    params = _run_python_step(
        _step(template, "build_pagination_params"),
        tenant_id="00000000-0000-0000-0000-000000000001",
        page_size=25,
        page_token="next-page",
    )

    assert params == {
        "tenant_id": "00000000-0000-0000-0000-000000000001",
        "pagination[page_size]": 25,
        "pagination[page_token]": "next-page",
    }


def test_create_detection_rule_matches_documented_contract() -> None:
    template = _load_template("create_detection_rule.yml")
    assert set(template.definition.expects) == {
        "tenant_id",
        "name",
        "description",
        "time_range_s",
        "run_frequency_s",
        "enabled_state_override",
        "severity",
        "query_text",
        "event_sink_ids",
        "tags",
        "sync_key",
        "base_url",
    }

    payload = _run_python_step(
        _step(template, "build_detection_rule"),
        tenant_id="00000000-0000-0000-0000-000000000001",
        name="Example detection",
        description="Detect an example event",
        time_range_s=300,
        run_frequency_s=300,
        enabled_state_override="Active",
        severity="Information",
        query_text="error | count",
        event_sink_ids=[],
        tags=None,
        sync_key=None,
    )

    assert payload["event_sink_ids"] == []
    assert "tags" not in payload
    assert "sync_key" not in payload


@pytest.mark.parametrize(
    ("filename", "step_ref", "resource_id_name"),
    [
        (
            "update_detection_rule.yml",
            "build_detection_rule_update",
            "detection_rule_id",
        ),
        ("update_event_sink.yml", "build_event_sink_update", "event_sink_id"),
    ],
)
def test_update_payload_uses_explicit_resource_id(
    filename: str,
    step_ref: str,
    resource_id_name: str,
) -> None:
    template = _load_template(filename)
    payload = _run_python_step(
        _step(template, step_ref),
        **{resource_id_name: "resource-id", "updates": {"id": "wrong-id"}},
    )

    assert payload == {"id": "resource-id"}


def test_list_event_sinks_uses_only_documented_tenant_parameter() -> None:
    template = _load_template("list_event_sinks.yml")
    assert set(template.definition.expects) == {"tenant_id", "base_url"}
    assert _step(template, "list_event_sinks").args["params"] == {
        "tenant_id": "${{ inputs.tenant_id }}"
    }


@pytest.mark.parametrize(
    ("filename", "step_ref", "path"),
    [
        (
            "validate_detection_rule_yaml.yml",
            "validate_detection_rule_yaml",
            "/v1/detection_rule_yaml/validate",
        ),
        (
            "run_detection_rule_yaml_tests.yml",
            "run_detection_rule_yaml_tests",
            "/v1/detection_rule_yaml/run_tests",
        ),
    ],
)
def test_detection_yaml_templates_send_raw_content(
    filename: str,
    step_ref: str,
    path: str,
) -> None:
    template = _load_template(filename)
    args = _step(template, step_ref).args

    assert args["url"].endswith(path)
    assert args["method"] == "POST"
    assert args["content"] == "${{ inputs.yaml_text }}"
    assert args["headers"]["Content-Type"] == "application/x-yaml"
    assert "payload" not in args
    assert "form_data" not in args
    assert "files" not in args
