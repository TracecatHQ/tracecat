#!/usr/bin/env python3
"""Guardedly add the activity exclusion to one Sentry workflow alert.

Only the current workflow-engine endpoint is supported.  A dry run is the
default; a write needs ``--apply`` and a digest copied from a reviewed dry run.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

ACTIVITY_BOUNDARY_KEY = "tracecat.capture.boundary"
ACTIVITY_BOUNDARY_VALUE = "activity"
TARGET_ACTION_TYPE = "sentry_app"
TARGET_ACTION_DISPLAY = "incident.io"
ACTIVITY_CONDITION_TYPE = "tagged_event"
ACTIVITY_CONDITION_MATCH = "ne"

_WORKFLOW_FIELDS = frozenset(
    {
        "id",
        "name",
        "organizationId",
        "createdBy",
        "dateCreated",
        "dateUpdated",
        "triggers",
        "actionFilters",
        "environment",
        "config",
        "detectorIds",
        "enabled",
        "lastTriggered",
        "owner",
    }
)
_GROUP_FIELDS = frozenset(
    {"id", "organizationId", "logicType", "conditions", "actions"}
)
_CONDITION_FIELDS = frozenset({"id", "type", "comparison", "conditionResult"})
_ACTION_FIELDS = frozenset({"id", "type", "integrationId", "data", "config", "status"})
_LOGIC_TYPES = frozenset({"all", "any", "any-short", "none"})
_OUTPUT_ONLY_FIELDS = frozenset({"dateUpdated", "lastTriggered"})
_ACTION_INPUT_FIELDS = (
    "id",
    "type",
    "data",
    "config",
    "integrationId",
    "status",
)


class ReconciliationError(Exception):
    """An operator-facing error that never includes a Sentry response body."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class DriftRefused(ReconciliationError):
    """The reviewed snapshot changed before a guarded write."""


class AmbiguousMutation(ReconciliationError):
    """A PUT may have reached Sentry and therefore must not be retried."""


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    body: bytes


class Transport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None = None,
    ) -> HttpResponse:
        """Send one request without following redirects."""
        ...


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


class UrllibTransport:
    """Stdlib transport with bearer auth and redirect refusal."""

    def __init__(self, token: str, timeout: float) -> None:
        self._token = token
        self._timeout = timeout
        self._opener = build_opener(_NoRedirectHandler())

    def request(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None = None,
    ) -> HttpResponse:
        request_headers = dict(headers)
        request_headers["Authorization"] = f"Bearer {self._token}"
        try:
            request = Request(
                url,
                data=body,
                headers=request_headers,
                method=method,
            )
            with self._opener.open(request, timeout=self._timeout) as response:
                return HttpResponse(response.status, response.read())
        except HTTPError as exc:
            # Do not read or expose the response body.  This also treats a
            # redirect as an error, so the token cannot cross an unexpected host.
            raise ReconciliationError(
                "http_error", f"Sentry API returned HTTP {exc.code} for {method}"
            ) from None
        except (URLError, TimeoutError, OSError, ValueError):
            # URL/proxy details can contain credentials or request data.
            raise ReconciliationError(
                "transport_error", f"Sentry API request failed for {method}"
            ) from None


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ReconciliationError(
            "invalid_json",
            "workflow contains values that cannot be represented as JSON",
        ) from exc


def snapshot_digest(workflow: Mapping[str, Any]) -> str:
    """Hash the complete, canonical GET response for the review guard."""

    return hashlib.sha256(_canonical_json(workflow)).hexdigest()


def normalize_base_url(raw_url: str) -> str:
    """Accept only an HTTPS origin/path without credentials or query state."""

    try:
        parsed = urlsplit(raw_url)
        scheme = parsed.scheme.lower()
        has_credentials = parsed.username is not None or parsed.password is not None
        port = parsed.port
    except (TypeError, ValueError):
        raise ReconciliationError("unsafe_url", "base URL is malformed") from None
    if scheme != "https" or not parsed.netloc:
        raise ReconciliationError(
            "unsafe_url", "base URL must use HTTPS and include a host"
        )
    if has_credentials:
        raise ReconciliationError("unsafe_url", "base URL must not contain credentials")
    if parsed.query or parsed.fragment:
        raise ReconciliationError(
            "unsafe_url", "base URL must not contain a query or fragment"
        )
    _ = port
    return urlunsplit(("https", parsed.netloc, parsed.path.rstrip("/"), "", ""))


def workflow_endpoint(base_url: str, organization: str, workflow_id: str) -> str:
    """Build only ``/api/0/organizations/{org}/workflows/{id}/``."""

    if not organization or not workflow_id:
        raise ReconciliationError(
            "invalid_identity", "organization and workflow ID are required"
        )
    base = normalize_base_url(base_url)
    return (
        f"{base}/api/0/organizations/{quote(organization, safe='')}/"
        f"workflows/{quote(workflow_id, safe='')}/"
    )


def _object(value: Any, location: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ReconciliationError("unexpected_shape", f"{location} must be an object")
    return dict(value)


def _array(value: Any, location: str) -> list[Any]:
    if not isinstance(value, list):
        raise ReconciliationError("unexpected_shape", f"{location} must be a list")
    return value


def _fields(value: Mapping[str, Any], expected: frozenset[str], location: str) -> None:
    if set(value) != expected:
        raise ReconciliationError(
            "unexpected_shape", f"{location} has an unexpected field shape"
        )


def _string(value: Any, location: str) -> str:
    if not isinstance(value, str) or not value:
        raise ReconciliationError(
            "unexpected_shape", f"{location} must be a non-empty string"
        )
    return value


def _validate_condition(value: Any, location: str) -> dict[str, Any]:
    condition = _object(value, location)
    _fields(condition, _CONDITION_FIELDS, location)
    _string(condition["id"], f"{location}.id")
    _string(condition["type"], f"{location}.type")
    return condition


def _validate_action(value: Any, location: str) -> dict[str, Any]:
    action = _object(value, location)
    _fields(action, _ACTION_FIELDS, location)
    _string(action["id"], f"{location}.id")
    _string(action["type"], f"{location}.type")
    if action["integrationId"] is not None and not isinstance(
        action["integrationId"], str
    ):
        raise ReconciliationError(
            "unexpected_shape", f"{location}.integrationId must be a string or null"
        )
    if not isinstance(action["data"], Mapping) or not isinstance(
        action["config"], Mapping
    ):
        raise ReconciliationError(
            "unexpected_shape", f"{location} action data and config must be objects"
        )
    _string(action["status"], f"{location}.status")
    return action


def _validate_group(value: Any, location: str, organization_id: str) -> dict[str, Any]:
    group = _object(value, location)
    _fields(group, _GROUP_FIELDS, location)
    _string(group["id"], f"{location}.id")
    if group["organizationId"] != organization_id:
        raise ReconciliationError(
            "identity_mismatch", f"{location} belongs to another organization"
        )
    if _string(group["logicType"], f"{location}.logicType") not in _LOGIC_TYPES:
        raise ReconciliationError(
            "unexpected_shape", f"{location}.logicType is unsupported"
        )
    for index, condition in enumerate(
        _array(group["conditions"], f"{location}.conditions")
    ):
        _validate_condition(condition, f"{location}.conditions[{index}]")
    for index, action in enumerate(_array(group["actions"], f"{location}.actions")):
        _validate_action(action, f"{location}.actions[{index}]")
    return group


def validate_workflow_shape(workflow: Mapping[str, Any]) -> dict[str, Any]:
    """Refuse legacy responses and unknown current-wire shapes."""

    result = _object(workflow, "workflow")
    _fields(result, _WORKFLOW_FIELDS, "workflow")
    _string(result["id"], "workflow.id")
    _string(result["name"], "workflow.name")
    organization_id = _string(result["organizationId"], "workflow.organizationId")
    if not isinstance(result["enabled"], bool):
        raise ReconciliationError(
            "unexpected_shape", "workflow.enabled must be boolean"
        )
    if result["environment"] is not None and not isinstance(result["environment"], str):
        raise ReconciliationError(
            "unexpected_shape", "workflow.environment must be a string or null"
        )
    if not isinstance(result["config"], Mapping):
        raise ReconciliationError(
            "unexpected_shape", "workflow.config must be an object"
        )
    for index, detector_id in enumerate(
        _array(result["detectorIds"], "workflow.detectorIds")
    ):
        _string(detector_id, f"workflow.detectorIds[{index}]")
    if result["owner"] is not None:
        _string(result["owner"], "workflow.owner")

    triggers = _validate_group(result["triggers"], "workflow.triggers", organization_id)
    if triggers["logicType"] != "any-short" or triggers["actions"]:
        raise ReconciliationError(
            "unexpected_shape",
            "workflow trigger group is not a current any-short group",
        )
    for index, action_filter in enumerate(
        _array(result["actionFilters"], "workflow.actionFilters")
    ):
        _validate_group(
            action_filter, f"workflow.actionFilters[{index}]", organization_id
        )
    return result


def _is_boundary(condition: Mapping[str, Any]) -> bool:
    if condition.get("type") != ACTIVITY_CONDITION_TYPE:
        return False
    comparison = condition.get("comparison")
    return (
        isinstance(comparison, Mapping)
        and isinstance(comparison.get("key"), str)
        and comparison["key"].casefold() == ACTIVITY_BOUNDARY_KEY.casefold()
    )


def _is_exact_boundary(condition: Mapping[str, Any]) -> bool:
    return (
        condition.get("type") == ACTIVITY_CONDITION_TYPE
        and condition.get("comparison")
        == {
            "key": ACTIVITY_BOUNDARY_KEY,
            "match": ACTIVITY_CONDITION_MATCH,
            "value": ACTIVITY_BOUNDARY_VALUE,
        }
        and condition.get("conditionResult") is True
    )


def _is_target_candidate(action: Mapping[str, Any]) -> bool:
    config = action.get("config")
    return (
        action.get("type") == TARGET_ACTION_TYPE
        and isinstance(config, Mapping)
        and config.get("targetDisplay") == TARGET_ACTION_DISPLAY
    )


def _is_target_action(action: Mapping[str, Any]) -> bool:
    config = action["config"]
    data = action["data"]
    return (
        _is_target_candidate(action)
        and isinstance(config, Mapping)
        and set(config) <= {"targetType", "targetIdentifier", "targetDisplay"}
        and config.get("targetType") == "sentry_app"
        and isinstance(config.get("targetIdentifier"), str)
        and config["targetIdentifier"].isascii()
        and config["targetIdentifier"].isdigit()
        and isinstance(data, Mapping)
        and set(data) <= {"settings"}
        and ("settings" not in data or isinstance(data["settings"], list))
    )


def _action_input(action: Mapping[str, Any]) -> dict[str, Any]:
    return {key: copy.deepcopy(action[key]) for key in _ACTION_INPUT_FIELDS}


def _filter_input(action_filter: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": copy.deepcopy(action_filter["id"]),
        "logicType": copy.deepcopy(action_filter["logicType"]),
        "conditions": copy.deepcopy(action_filter["conditions"]),
        "actions": [
            _action_input(action)
            for action in cast(list[Mapping[str, Any]], action_filter["actions"])
        ],
    }


def _minimal_payload(workflow: Mapping[str, Any]) -> dict[str, Any]:
    # WorkflowValidator requires name and accepts partial updates.  Supplying
    # only these fields avoids rewriting config, triggers, owners, or detectors.
    return {
        "name": copy.deepcopy(workflow["name"]),
        "enabled": copy.deepcopy(workflow["enabled"]),
        "actionFilters": [
            _filter_input(action_filter)
            for action_filter in cast(
                list[Mapping[str, Any]], workflow["actionFilters"]
            )
        ],
    }


def _plan_projection(workflow: Mapping[str, Any], target_index: int) -> dict[str, Any]:
    projection = {
        key: copy.deepcopy(workflow[key])
        for key in _WORKFLOW_FIELDS
        if key not in _OUTPUT_ONLY_FIELDS
    }
    filters = cast(list[dict[str, Any]], projection["actionFilters"])
    filters[target_index]["conditions"] = []
    return projection


@dataclass(frozen=True)
class ReconciliationPlan:
    source: dict[str, Any]
    payload: dict[str, Any]
    source_digest: str
    target_filter_index: int
    already_excluded: bool

    @property
    def changed(self) -> bool:
        return not self.already_excluded

    def public_document(self, mode: str, result: str | None = None) -> dict[str, Any]:
        return {
            "schema": "tracecat.sentry-workflow-reconciliation/v1",
            "mode": mode,
            "result": result or ("would-update" if self.changed else "no-op"),
            "snapshot_sha256": self.source_digest,
            "delta": {
                "target_filter_index": self.target_filter_index
                if self.changed
                else None,
                "appended_conditions": 1 if self.changed else 0,
                "appended_condition": (
                    {
                        "type": ACTIVITY_CONDITION_TYPE,
                        "comparison": {
                            "key": ACTIVITY_BOUNDARY_KEY,
                            "match": ACTIVITY_CONDITION_MATCH,
                            "value": ACTIVITY_BOUNDARY_VALUE,
                        },
                        "conditionResult": True,
                    }
                    if self.changed
                    else None
                ),
                "target_action_groups": 1,
            },
            "preserved": [
                "workflow metadata",
                "trigger group and conditions",
                "target action and settings",
                "unrelated action filters and routes",
                "detector connections",
            ],
        }


def _make_plan(
    workflow: Mapping[str, Any],
    *,
    expected_environment: str,
    expected_name: str | None = None,
    expected_organization_id: str | None = None,
    expected_detector_ids: Sequence[str] | None = None,
    expected_workflow_id: str | None = None,
) -> ReconciliationPlan:
    source = validate_workflow_shape(workflow)
    if expected_workflow_id is not None and source["id"] != expected_workflow_id:
        raise ReconciliationError(
            "identity_mismatch", "workflow ID does not match baseline"
        )
    if expected_name is not None and source["name"] != expected_name:
        raise ReconciliationError(
            "identity_mismatch", "workflow name does not match baseline"
        )
    if (
        expected_organization_id is not None
        and source["organizationId"] != expected_organization_id
    ):
        raise ReconciliationError(
            "identity_mismatch", "workflow organization does not match baseline"
        )
    if source["environment"] != expected_environment:
        raise ReconciliationError(
            "identity_mismatch", "workflow environment does not match baseline"
        )
    if source["enabled"] is not True:
        raise ReconciliationError("disabled_workflow", "workflow is disabled")
    if expected_detector_ids is not None and list(source["detectorIds"]) != list(
        expected_detector_ids
    ):
        raise ReconciliationError(
            "identity_mismatch", "workflow detector connections do not match baseline"
        )

    action_filters = cast(list[dict[str, Any]], source["actionFilters"])
    target_indexes: list[int] = []
    target_actions: list[dict[str, Any]] = []
    boundary_conditions: list[tuple[int, dict[str, Any]]] = []
    for index, action_filter in enumerate(action_filters):
        actions = cast(list[dict[str, Any]], action_filter["actions"])
        candidates = [action for action in actions if _is_target_candidate(action)]
        if candidates and any(not _is_target_action(action) for action in candidates):
            raise ReconciliationError(
                "unexpected_target",
                "incident.io action has an unexpected configuration",
            )
        matching = [action for action in candidates if _is_target_action(action)]
        if matching:
            target_indexes.append(index)
            target_actions.extend(matching)
        for condition in cast(list[dict[str, Any]], action_filter["conditions"]):
            if _is_boundary(condition):
                boundary_conditions.append((index, condition))

    if len(target_indexes) != 1 or len(target_actions) != 1:
        raise ReconciliationError(
            "ambiguous_target", "expected exactly one incident.io action group"
        )
    target_index = target_indexes[0]
    target_group = action_filters[target_index]
    if target_group["logicType"] != "all":
        raise ReconciliationError(
            "unsafe_logic", "incident.io action group must use ALL logic"
        )
    if target_actions[0]["status"] != "active":
        raise ReconciliationError("disabled_target", "incident.io action is not active")
    if len(cast(list[Any], target_group["actions"])) != 1:
        raise ReconciliationError(
            "mixed_actions", "incident.io action group contains mixed actions"
        )

    exact = [
        (index, condition)
        for index, condition in boundary_conditions
        if _is_exact_boundary(condition)
    ]
    conflicting = [
        (index, condition)
        for index, condition in boundary_conditions
        if not _is_exact_boundary(condition)
    ]
    if (
        conflicting
        or any(index != target_index for index, _ in exact)
        or len(exact) > 1
    ):
        raise ReconciliationError(
            "conflicting_boundary", "workflow already has a conflicting activity filter"
        )

    payload = _minimal_payload(source)
    if exact:
        return ReconciliationPlan(
            source=source,
            payload=payload,
            source_digest=snapshot_digest(source),
            target_filter_index=target_index,
            already_excluded=True,
        )
    cast(
        list[dict[str, Any]], payload["actionFilters"][target_index]["conditions"]
    ).append(
        {
            "type": ACTIVITY_CONDITION_TYPE,
            "comparison": {
                "key": ACTIVITY_BOUNDARY_KEY,
                "match": ACTIVITY_CONDITION_MATCH,
                "value": ACTIVITY_BOUNDARY_VALUE,
            },
            "conditionResult": True,
        }
    )
    return ReconciliationPlan(
        source=source,
        payload=payload,
        source_digest=snapshot_digest(source),
        target_filter_index=target_index,
        already_excluded=False,
    )


def _decode_response(response: HttpResponse, method: str) -> dict[str, Any]:
    if not 200 <= response.status_code < 300:
        raise ReconciliationError(
            "http_error",
            f"Sentry API returned HTTP {response.status_code} for {method}",
        )
    try:
        value = json.loads(response.body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReconciliationError(
            "invalid_response", f"Sentry API returned invalid JSON for {method}"
        ) from exc
    return _object(value, f"Sentry API {method} response")


class SentryWorkflowClient:
    def __init__(self, endpoint: str, transport: Transport) -> None:
        self._endpoint = endpoint
        self._transport = transport

    def get(self) -> dict[str, Any]:
        response = self._transport.request(
            "GET", self._endpoint, {"Accept": "application/json"}
        )
        return _decode_response(response, "GET")

    def put(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        try:
            response = self._transport.request(
                "PUT",
                self._endpoint,
                {
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                _canonical_json(payload),
            )
            return _decode_response(response, "PUT")
        except AmbiguousMutation:
            raise
        except ReconciliationError as exc:
            raise AmbiguousMutation(
                "ambiguous_mutation",
                "PUT outcome is ambiguous; inspect the workflow before retrying",
            ) from exc


@dataclass(frozen=True)
class ReconciliationResult:
    plan: ReconciliationPlan
    result: str
    after_digest: str | None = None

    def public_document(self, mode: str) -> dict[str, Any]:
        document = self.plan.public_document(mode, self.result)
        if self.after_digest is not None:
            document["verified_snapshot_sha256"] = self.after_digest
        return document


def _verify_conditions(
    expected: list[dict[str, Any]], actual: list[dict[str, Any]]
) -> bool:
    if len(expected) != len(actual):
        return False
    for expected_condition, actual_condition in zip(expected, actual, strict=True):
        if "id" in expected_condition:
            if actual_condition != expected_condition:
                return False
            continue
        if (
            set(actual_condition) != _CONDITION_FIELDS
            or not actual_condition.get("id")
            or {key: value for key, value in actual_condition.items() if key != "id"}
            != expected_condition
        ):
            return False
    return True


def _verify_after(
    before: Mapping[str, Any],
    expected_payload: Mapping[str, Any],
    after: Mapping[str, Any],
    target_index: int,
) -> None:
    checked_after = validate_workflow_shape(after)
    before_filters = cast(list[Any], before["actionFilters"])
    after_filters = cast(list[Any], checked_after["actionFilters"])
    if len(after_filters) != len(before_filters) or target_index >= len(after_filters):
        raise ReconciliationError(
            "verification_failed", "Sentry changed the number of action filters"
        )
    if _plan_projection(checked_after, target_index) != _plan_projection(
        before, target_index
    ):
        raise ReconciliationError(
            "verification_failed", "Sentry changed protected workflow data"
        )
    expected_filters = cast(list[dict[str, Any]], expected_payload["actionFilters"])
    actual_filters = cast(list[dict[str, Any]], after_filters)
    expected_group = expected_filters[target_index]
    actual_group = _filter_input(actual_filters[target_index])
    actual_without_conditions = {
        key: value for key, value in actual_group.items() if key != "conditions"
    }
    expected_without_conditions = {
        key: value for key, value in expected_group.items() if key != "conditions"
    }
    if actual_without_conditions != expected_without_conditions:
        raise ReconciliationError(
            "verification_failed", "Sentry did not retain the reconciled action filter"
        )
    if not _verify_conditions(
        cast(list[dict[str, Any]], expected_group["conditions"]),
        cast(list[dict[str, Any]], actual_group["conditions"]),
    ):
        raise ReconciliationError(
            "verification_failed", "Sentry did not retain the reconciled conditions"
        )


def reconcile(
    *,
    base_url: str,
    organization: str,
    workflow_id: str,
    expected_environment: str,
    expected_name: str | None = None,
    expected_organization_id: str | None = None,
    expected_detector_ids: Sequence[str] | None = None,
    apply: bool = False,
    expected_digest: str | None = None,
    transport: Transport | None = None,
    timeout: float = 10.0,
) -> ReconciliationResult:
    """Plan one update, or execute it with a reviewed digest guard."""

    if not expected_environment:
        raise ReconciliationError(
            "invalid_identity", "expected environment is required"
        )
    if expected_digest is not None and (
        len(expected_digest) != 64
        or any(character not in "0123456789abcdef" for character in expected_digest)
    ):
        raise ReconciliationError(
            "invalid_digest", "expected digest must be SHA-256 hex"
        )
    if timeout <= 0:
        raise ReconciliationError("invalid_timeout", "timeout must be positive")
    endpoint = workflow_endpoint(base_url, organization, workflow_id)
    if transport is None:
        token = os.environ.get("SENTRY_AUTH_TOKEN")
        if not token:
            raise ReconciliationError(
                "missing_token", "SENTRY_AUTH_TOKEN is required for Sentry API access"
            )
        transport = UrllibTransport(token, timeout)
    client = SentryWorkflowClient(endpoint, transport)
    first_snapshot = client.get()
    first_plan = _make_plan(
        first_snapshot,
        expected_environment=expected_environment,
        expected_name=expected_name,
        expected_organization_id=expected_organization_id,
        expected_detector_ids=expected_detector_ids,
        expected_workflow_id=workflow_id,
    )

    # A no-op is safe even when an earlier successful run changed the reviewed
    # digest.  There is no write to guard in this branch.
    if not first_plan.changed:
        return ReconciliationResult(plan=first_plan, result="no-op")
    if not apply:
        if expected_digest is not None and first_plan.source_digest != expected_digest:
            raise DriftRefused("drift_refused", "workflow differs from reviewed digest")
        return ReconciliationResult(plan=first_plan, result="would-update")
    if expected_digest is None:
        raise ReconciliationError(
            "missing_digest", "--apply requires the reviewed dry-run digest"
        )
    if first_plan.source_digest != expected_digest:
        raise DriftRefused("drift_refused", "workflow differs from reviewed digest")

    second_snapshot = client.get()
    second_plan = _make_plan(
        second_snapshot,
        expected_environment=expected_environment,
        expected_name=expected_name,
        expected_organization_id=expected_organization_id,
        expected_detector_ids=expected_detector_ids,
        expected_workflow_id=workflow_id,
    )
    if second_plan.source_digest != first_plan.source_digest:
        raise DriftRefused("drift_refused", "workflow changed before the guarded write")
    client.put(second_plan.payload)
    verified = client.get()
    _verify_after(
        before=second_plan.source,
        expected_payload=second_plan.payload,
        after=verified,
        target_index=second_plan.target_filter_index,
    )
    return ReconciliationResult(
        plan=second_plan,
        result="applied",
        after_digest=snapshot_digest(verified),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Dry-run or guardedly reconcile Sentry activity paging."
    )
    parser.add_argument(
        "--base-url", required=True, help="HTTPS Sentry region base URL"
    )
    parser.add_argument(
        "--organization", required=True, help="Sentry organization slug or ID"
    )
    parser.add_argument("--workflow-id", required=True, help="Modern workflow alert ID")
    parser.add_argument("--expected-environment", required=True)
    parser.add_argument(
        "--expected-name", required=True, help="Exact workflow name identity baseline"
    )
    parser.add_argument("--expected-organization-id")
    parser.add_argument(
        "--expected-detector-id",
        action="append",
        dest="expected_detector_ids",
        help="Expected detector connection; repeat for each ID",
    )
    parser.add_argument(
        "--expected-digest",
        help="SHA-256 from a reviewed dry run; required when a write is needed",
    )
    parser.add_argument("--apply", action="store_true", help="Perform the guarded PUT")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument(
        "--plan-file", type=Path, help="Write sanitized plan JSON locally"
    )
    return parser


def _write_plan(document: Mapping[str, Any], plan_file: Path | None) -> None:
    rendered = json.dumps(document, indent=2, sort_keys=True) + "\n"
    print(rendered, end="")
    if plan_file is not None:
        try:
            plan_file.write_text(rendered, encoding="utf-8")
        except OSError:
            raise ReconciliationError(
                "plan_write_failed", "could not write the local plan file"
            ) from None


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = reconcile(
            base_url=args.base_url,
            organization=args.organization,
            workflow_id=args.workflow_id,
            expected_environment=args.expected_environment,
            expected_name=args.expected_name,
            expected_organization_id=args.expected_organization_id,
            expected_detector_ids=args.expected_detector_ids,
            apply=args.apply,
            expected_digest=args.expected_digest,
            timeout=args.timeout,
        )
        _write_plan(
            result.public_document("apply" if args.apply else "dry-run"),
            args.plan_file,
        )
    except ReconciliationError as exc:
        print(f"error[{exc.code}]: {exc.message}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
