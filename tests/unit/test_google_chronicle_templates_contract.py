"""Shared structural contracts for the Google Chronicle templates."""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from tracecat_registry import RegistryOAuthSecret

from tracecat.registry.actions.schemas import TemplateAction

TEMPLATES = (
    Path(__file__).resolve().parents[2]
    / "packages"
    / "tracecat-registry"
    / "tracecat_registry"
    / "templates"
    / "tools"
)
CHRONICLE = TEMPLATES / "google_chronicle"
CHRONICLE_TEMPLATES = sorted(CHRONICLE.rglob("*.yml"))
CALL_API = "tools.google_chronicle.call_api"
DOC_URL_PREFIX = "https://docs.cloud.google.com/chronicle/docs/reference/rest/"
BASE_URL = "${{ inputs.base_url || VARS.google_chronicle.base_url }}"
INSTANCE = "${{ inputs.instance }}"

# This is an explicit copy of the reviewed Google REST contract. The generic
# transport stays flexible; the purpose-built YAML actions pin their documented
# method, version, route, query parameters, and request fields here.
# fmt: off
EXPECTED_CONTRACTS: dict[str, tuple[str, str, str, list[str], list[str]]] = {
    "change_case_alert_priority": ("PATCH", "v1", "/cases/${{ inputs.case }}/caseAlerts/${{ inputs.case_alert }}", ["updateMask"], ["priority"]),
    "close_case_alert": ("PATCH", "v1", "/cases/${{ inputs.case }}/caseAlerts/${{ inputs.case_alert }}", ["updateMask"], ["closureDetails", "status"]),
    "get_case_alert": ("GET", "v1", "/cases/${{ inputs.case }}/caseAlerts/${{ inputs.case_alert }}", ["expand"], []),
    "list_case_alerts": ("GET", "v1", "/cases/${{ inputs.case }}/caseAlerts", ["distinctBy", "expand", "filter", "orderBy", "pageSize", "pageToken"], []),
    "reopen_case_alert": ("PATCH", "v1", "/cases/${{ inputs.case }}/caseAlerts/${{ inputs.case_alert }}", ["updateMask"], ["status"]),
    "add_case_tag": ("POST", "v1", "/cases/${{ inputs.case }}:addTag", [], ["tag"]),
    "assign_case": ("PATCH", "v1", "/cases/${{ inputs.case }}", ["updateMask"], ["assignee"]),
    "bulk_add_case_tag": ("POST", "v1", "/cases:executeBulkAddTag", [], ["casesIds", "tags"]),
    "bulk_assign_cases": ("POST", "v1", "/cases:executeBulkAssign", [], ["casesIds", "userName"]),
    "bulk_change_case_priority": ("POST", "v1", "/cases:executeBulkChangePriority", [], ["casesIds", "priority"]),
    "bulk_change_case_stage": ("POST", "v1", "/cases:executeBulkChangeStage", [], ["casesIds", "stage"]),
    "bulk_close_cases": ("POST", "v1", "/cases:executeBulkClose", [], ["casesIds", "closeComment", "closeReason", "dynamicParameters", "rootCause"]),
    "bulk_reopen_cases": ("POST", "v1", "/cases:executeBulkReopen", [], ["casesIds", "reopenComment"]),
    "change_case_priority": ("PATCH", "v1", "/cases/${{ inputs.case }}", ["updateMask"], ["priority"]),
    "change_case_stage": ("PATCH", "v1", "/cases/${{ inputs.case }}", ["updateMask"], ["stage"]),
    "get_case": ("GET", "v1", "/cases/${{ inputs.case }}", ["expand"], []),
    "list_cases": ("GET", "v1", "/cases", ["distinctBy", "expand", "filter", "orderBy", "pageSize", "pageToken"], []),
    "search_cases": ("POST", "v1alpha", "/legacySearches:legacyCaseSearchEverything", [], ["assignedUsers", "caseComment", "caseSource", "categoryOutcomes", "closeReason", "disableTimeRangeLocalization", "endTime", "environments", "externalAlertId", "importance", "incident", "involvedEntity", "isCaseClosed", "pageSize", "paging", "ports", "priorities", "products", "requestedPage", "ruleGenerator", "searchTerm", "sortBy", "stage", "startTime", "tags", "timeRangeFilter", "title"]),
    "create_case_comment": ("POST", "v1", "/cases/${{ inputs.case }}/caseComments", [], ["alertIdentifier", "caseAttachment", "comment", "isFavorite"]),
    "delete_case_comment": ("DELETE", "v1", "/cases/${{ inputs.case }}/caseComments/${{ inputs.case_comment }}", [], []),
    "get_case_comment": ("GET", "v1", "/cases/${{ inputs.case }}/caseComments/${{ inputs.case_comment }}", ["expand"], []),
    "list_case_comments": ("GET", "v1", "/cases/${{ inputs.case }}/caseComments", ["expand", "filter", "orderBy", "pageSize", "pageToken"], []),
    "update_case_comment": ("PATCH", "v1", "/cases/${{ inputs.case }}/caseComments/${{ inputs.case_comment }}", ["updateMask"], ["alertIdentifier", "caseAttachment", "comment", "isFavorite"]),
    "get_alert": ("GET", "v1alpha", "/legacy:legacyGetAlert", ["alertId", "includeDetections"], []),
    "get_detection": ("GET", "v1alpha", "/legacy:legacyGetDetection", ["detectionId", "ruleId"], []),
    "get_event_for_detection": ("GET", "v1alpha", "/legacy:legacyGetEventForDetection", ["detectionId", "nextPageToken", "pageSize"], []),
    "list_detections": ("GET", "v1alpha", "/legacy:legacySearchDetections", ["alertState", "endTime", "includeNestedDetections", "includeSimulatedDetections", "listBasis", "maxRespSizeBytes", "pageSize", "pageToken", "ruleId", "simulatedDataVisibility", "startTime"], []),
    "search_alerts": ("GET", "v1alpha", "/legacy:legacyFetchAlertsView", ["alertListOptions.entityIndicator", "alertListOptions.maxReturnedAlerts", "baselineQuery", "enableCache", "fieldAggregationOptions.maxValuesPerField", "includeNonAlertingDetections", "maxResponseAlertsBytes", "simulatedDataVisibility", "snapshotQuery", "timeRange.endTime", "timeRange.startTime"], []),
    "search_findings": ("GET", "v1alpha", "/legacy:legacySearchFindings", ["findingType", "nextPageToken", "pageSize", "timestampRange.endTime", "timestampRange.startTime"], []),
    "update_alert": ("POST", "v1alpha", "/legacy:legacyUpdateAlert", [], ["alertId", "caseName", "feedback", "responsePlatformInfo"]),
    "execute_query": ("GET", "v1", ":udmSearch", ["limit", "query", "queryDialect", "timeRange.endTime", "timeRange.startTime"], []),
    "find_entity": ("GET", "v1", ":findEntity", ["entityNamespace", "indicator", "referenceTime", "udmField"], []),
    "find_entity_alerts": ("GET", "v1", ":findEntityAlerts", ["entityId", "fieldAndValue", "timeRange.endTime", "timeRange.startTime"], []),
    "find_related_entities": ("GET", "v1", ":findRelatedEntities", ["domainType", "entityId", "entityTypes", "excludeFirstLastSeen", "fieldAndValue", "includeAllUdmEventTypesForFirstLastSeen", "limit", "timeRange.endTime", "timeRange.startTime"], []),
    "find_udm_field_values": ("GET", "v1", ":findUdmFieldValues", ["limit", "query"], []),
    "cancel_operation": ("POST", "v1", "/operations/${{ inputs.operation }}:cancel", [], []),
    "create_retrohunt": ("POST", "v1", "/rules/${{ inputs.rule }}/retrohunts", [], ["processInterval"]),
    "get_operation": ("GET", "v1", "/operations/${{ inputs.operation }}", [], []),
    "get_retrohunt": ("GET", "v1", "/rules/${{ inputs.rule }}/retrohunts/${{ inputs.retrohunt }}", [], []),
    "list_operations": ("GET", "v1", "/operations", ["filter", "pageSize", "pageToken", "returnPartialSuccess"], []),
    "list_retrohunts": ("GET", "v1", "/rules/${{ inputs.rule }}/retrohunts", ["filter", "pageSize", "pageToken"], []),
    "create_rule": ("POST", "v1", "/rules", [], ["scope", "text"]),
    "delete_rule": ("DELETE", "v1", "/rules/${{ inputs.rule }}", ["force"], []),
    "disable_rule": ("PATCH", "v1", "/rules/${{ inputs.rule }}/deployment", ["updateMask"], ["enabled"]),
    "enable_rule": ("PATCH", "v1", "/rules/${{ inputs.rule }}/deployment", ["updateMask"], ["enabled"]),
    "get_rule": ("GET", "v1", "/rules/${{ inputs.rule }}", ["view"], []),
    "get_rule_deployment": ("GET", "v1", "/rules/${{ inputs.rule }}/deployment", [], []),
    "list_rule_deployments": ("GET", "v1", "/rules/-/deployments", ["filter", "pageSize", "pageToken"], []),
    "list_rule_execution_errors": ("GET", "v1", "/ruleExecutionErrors", ["filter", "pageSize", "pageToken"], []),
    "list_rule_revisions": ("GET", "v1", "/rules/${{ inputs.rule }}:listRevisions", ["pageSize", "pageToken", "view"], []),
    "list_rules": ("GET", "v1", "/rules", ["filter", "orderBy", "pageSize", "pageToken", "skip", "view"], []),
    "test_rule": ("POST", "v1alpha", "/legacy:legacyRunTestRule", [], ["maxResults", "ruleText", "scope", "timeRange"]),
    "update_rule": ("PATCH", "v1", "/rules/${{ inputs.rule }}", ["updateMask"], ["scope", "text"]),
    "update_rule_deployment": ("PATCH", "v1", "/rules/${{ inputs.rule }}/deployment", ["updateMask"], ["alerting", "archived", "enabled", "runFrequency", "scheduleCustomizations"]),
    "verify_rule_text": ("POST", "v1", ":verifyRuleText", [], ["ruleText"]),
}
# fmt: on


def test_the_namespace_is_not_empty() -> None:
    assert CHRONICLE_TEMPLATES


@pytest.mark.parametrize(
    "path", CHRONICLE_TEMPLATES, ids=lambda p: str(p.relative_to(CHRONICLE))
)
def test_chronicle_template_contract(path: Path) -> None:
    definition = TemplateAction.from_yaml(path).definition

    assert definition.namespace == "tools.google_chronicle"

    # --- Credentials: exactly the two Chronicle grants, both optional ---
    secrets = definition.secrets or []
    assert all(isinstance(secret, RegistryOAuthSecret) for secret in secrets), (
        "Chronicle authenticates through OAuth only; no key secret belongs here"
    )
    oauth_secrets = [s for s in secrets if isinstance(s, RegistryOAuthSecret)]
    assert [(s.provider_id, s.grant_type) for s in oauth_secrets] == [
        ("google_chronicle", "authorization_code"),
        ("google_chronicle", "client_credentials"),
    ]
    assert all(secret.optional for secret in oauth_secrets), (
        "both grants are optional so `call_api` can pick whichever is configured"
    )

    # --- Dispatch: one shared boundary, never a raw HTTP call ---
    steps = definition.steps
    assert [step.action for step in steps].count(CALL_API) == 1
    call_step = next(step for step in steps if step.action == CALL_API)
    assert not any(
        step.action.startswith(
            ("core.http_request", "core.http_poll", "core.http_paginate")
        )
        for step in steps
    ), "Chronicle requests must go through call_api, not core.http_*"

    args: Mapping[str, Any] = call_step.args
    assert args["url"].startswith(f"{BASE_URL}/")
    assert set(args) <= {"url", "method", "params", "payload"}
    assert definition.expects is not None
    assert definition.expects["base_url"].default is None

    # --- No credential ever travels as an action input ---
    assert "access_token" not in args
    for key, value in args.items():
        assert "SECRETS." not in str(value), (
            f"`{key}` passes a secret to call_api; call_api owns the token chain"
        )
    assert "token" not in (definition.expects or {})

    # --- Output: the provider body returned by call_api ---
    assert definition.returns == f"${{{{ steps.{call_step.ref}.result }}}}"

    # --- Documentation deep-links to the exact REST method ---
    assert definition.doc_url is not None
    assert definition.doc_url.startswith(DOC_URL_PREFIX)


def _call_step(path: Path) -> Any:
    definition = TemplateAction.from_yaml(path).definition
    return next(step for step in definition.steps if step.action == CALL_API)


def _top_level_keys(value: Any) -> list[str]:
    if isinstance(value, Mapping):
        return sorted(str(key) for key in value)
    if value is None:
        return []
    return ["<expression>"]


def test_every_purpose_built_action_matches_the_reviewed_rest_contract() -> None:
    actual: dict[str, tuple[str, str, str, list[str], list[str]]] = {}
    for path in CHRONICLE_TEMPLATES:
        definition = TemplateAction.from_yaml(path).definition
        action_name = definition.action.rsplit(".", 1)[-1]
        call_step = next(step for step in definition.steps if step.action == CALL_API)
        args: Mapping[str, Any] = call_step.args
        _, version, path_suffix, _, _ = EXPECTED_CONTRACTS[action_name]
        assert args["url"] == f"{BASE_URL}/{version}/{INSTANCE}{path_suffix}"
        actual[action_name] = (
            args["method"],
            version,
            path_suffix,
            _top_level_keys(args.get("params")),
            _top_level_keys(args.get("payload")),
        )

    assert actual == EXPECTED_CONTRACTS


PATCH_TEMPLATES = [
    path for path in CHRONICLE_TEMPLATES if _call_step(path).args["method"] == "PATCH"
]


@pytest.mark.parametrize(
    "path", PATCH_TEMPLATES, ids=lambda p: str(p.relative_to(CHRONICLE))
)
def test_partial_update_declares_an_update_mask(path: Path) -> None:
    """A PATCH without `updateMask` overwrites every field the body omits."""
    call_step = _call_step(path)
    params = call_step.args.get("params")
    assert isinstance(params, dict) and params.get("updateMask"), (
        "a PATCH template must pin the fields it updates"
    )
    assert call_step.args.get("payload"), "a PATCH template must send the masked fields"


@pytest.mark.parametrize(
    "path", CHRONICLE_TEMPLATES, ids=lambda p: str(p.relative_to(CHRONICLE))
)
def test_no_auto_pagination(path: Path) -> None:
    """Paging inputs are exposed; the loop belongs to the workflow author."""
    definition = TemplateAction.from_yaml(path).definition
    assert "max_pages" not in (definition.expects or {})


def test_action_names_are_unique() -> None:
    actions = [
        TemplateAction.from_yaml(path).definition.action for path in CHRONICLE_TEMPLATES
    ]
    assert len(actions) == len(set(actions))


def test_execute_query_uses_the_documented_udm_search_contract() -> None:
    """The generic query action is the one place the request shape is fixed."""
    definition = TemplateAction.from_yaml(
        CHRONICLE / "hunting" / "execute_query.yml"
    ).definition
    expects = definition.expects or {}
    assert set(expects) == {
        "base_url",
        "instance",
        "query",
        "start_time",
        "end_time",
        "limit",
        "query_dialect",
    }
    assert expects["limit"].default == 100
    assert expects["query_dialect"].default == "YL2"

    call_step = next(step for step in definition.steps if step.action == CALL_API)
    assert call_step.args["method"] == "GET"
    assert call_step.args["url"] == f"{BASE_URL}/v1/{INSTANCE}:udmSearch"
    assert call_step.args["params"] == {
        "query": "${{ inputs.query }}",
        "timeRange.startTime": "${{ inputs.start_time }}",
        "timeRange.endTime": "${{ inputs.end_time }}",
        "limit": "${{ inputs.limit }}",
        "queryDialect": "${{ inputs.query_dialect }}",
    }
    assert "payload" not in call_step.args, "udmSearch takes an empty request body"


def test_templates_do_not_declare_provider_enums() -> None:
    for path in CHRONICLE_TEMPLATES:
        assert "enum[" not in path.read_text(encoding="utf-8")
