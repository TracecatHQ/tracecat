from pathlib import Path
from typing import Any

from tracecat.dsl.schemas import MaterializedTaskResult, TemplateExecutionContext
from tracecat.expressions.eval import eval_templated_object
from tracecat.registry.actions.schemas import ActionStep, TemplateAction

TEMPLATE_ROOT = Path(
    "packages/tracecat-registry/tracecat_registry/templates/tools/microsoft_sentinel"
)

SUBSCRIPTION_ID = "00000000-1111-2222-3333-444444444444"
RESOURCE_GROUP_NAME = "example-resource-group"
WORKSPACE_NAME = "example-workspace"
INCIDENT_ID = "55555555-6666-7777-8888-999999999999"
INCIDENT_ARM_ID = (
    f"/subscriptions/{SUBSCRIPTION_ID}/resourceGroups/{RESOURCE_GROUP_NAME}/"
    f"providers/Microsoft.OperationalInsights/workspaces/{WORKSPACE_NAME}/"
    f"providers/Microsoft.SecurityInsights/Incidents/{INCIDENT_ID}"
)


def _load_template(path: str) -> TemplateAction:
    return TemplateAction.from_yaml(TEMPLATE_ROOT / path)


def _step(action: TemplateAction, ref: str) -> ActionStep:
    return next(step for step in action.definition.steps if step.ref == ref)


def _run_python_step(
    step: ActionStep, context: TemplateExecutionContext
) -> dict[str, Any]:
    inputs = eval_templated_object(step.args["inputs"], operand=context)
    namespace: dict[str, Any] = {}
    exec(step.args["script"], namespace)  # noqa: S102
    return namespace["main"](**inputs)


def _materialized_result(result: Any) -> MaterializedTaskResult:
    return {
        "result": result,
        "result_typename": type(result).__name__,
        "error": None,
        "error_typename": None,
        "interaction": None,
        "interaction_id": None,
        "interaction_type": None,
    }


def _render_http_url(
    template_path: str,
    http_ref: str,
    inputs: dict[str, Any],
) -> str:
    action = _load_template(template_path)
    context = TemplateExecutionContext(inputs=inputs, steps={})

    normalize_step = _step(action, "normalize_ids")
    normalized = _run_python_step(normalize_step, context)
    context["steps"]["normalize_ids"] = _materialized_result(normalized)

    http_step = _step(action, http_ref)
    return eval_templated_object(http_step.args["url"], operand=context)


def _sentinel_inputs(incident_id: str) -> dict[str, Any]:
    return {
        "base_url": "https://management.azure.com",
        "subscription_id": SUBSCRIPTION_ID,
        "resource_group_name": RESOURCE_GROUP_NAME,
        "workspace_name": WORKSPACE_NAME,
        "incident_id": incident_id,
    }


def test_get_incident_normalizes_full_arm_id() -> None:
    url = _render_http_url(
        "incidents/get_incident.yml",
        "get_incident",
        _sentinel_inputs(INCIDENT_ARM_ID),
    )

    assert url == (
        f"https://management.azure.com/subscriptions/{SUBSCRIPTION_ID}"
        f"/resourceGroups/{RESOURCE_GROUP_NAME}"
        f"/providers/Microsoft.OperationalInsights/workspaces/{WORKSPACE_NAME}"
        f"/providers/Microsoft.SecurityInsights/incidents/{INCIDENT_ID}"
    )
    assert url.count("/subscriptions/") == 1


def test_list_incident_comments_normalizes_full_arm_id() -> None:
    url = _render_http_url(
        "incidents/list_incident_comments.yml",
        "list_comments",
        _sentinel_inputs(INCIDENT_ARM_ID),
    )

    assert url.endswith(f"/incidents/{INCIDENT_ID}/comments")
    assert url.count("/subscriptions/") == 1


def test_get_incident_keeps_plain_incident_id() -> None:
    url = _render_http_url(
        "incidents/get_incident.yml",
        "get_incident",
        _sentinel_inputs(INCIDENT_ID),
    )

    assert url.endswith(f"/incidents/{INCIDENT_ID}")
    assert url.count("/subscriptions/") == 1
