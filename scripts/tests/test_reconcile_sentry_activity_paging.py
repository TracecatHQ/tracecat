from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import pytest

from scripts.monitoring.reconcile_sentry_activity_paging import (
    ACTIVITY_BOUNDARY_KEY,
    ACTIVITY_BOUNDARY_VALUE,
    AmbiguousMutation,
    DriftRefused,
    HttpResponse,
    ReconciliationError,
    reconcile,
    snapshot_digest,
    workflow_endpoint,
)


@dataclass
class FakeResponse:
    value: Mapping[str, Any]
    status_code: int = 200

    def as_http_response(self) -> HttpResponse:
        return HttpResponse(
            status_code=self.status_code,
            body=json.dumps(self.value).encode("utf-8"),
        )


class FakeTransport:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, Mapping[str, str], bytes | None]] = []

    def request(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None = None,
    ) -> HttpResponse:
        self.calls.append((method, url, headers, body))
        if not self.responses:
            raise AssertionError("fake transport received an unexpected request")
        return self.responses.pop(0).as_http_response()


def _condition(
    condition_id: str,
    *,
    condition_type: str = "first_seen_event",
    comparison: Any = True,
    condition_result: Any = True,
) -> dict[str, Any]:
    return {
        "id": condition_id,
        "type": condition_type,
        "comparison": comparison,
        "conditionResult": condition_result,
    }


def _action(
    action_id: str,
    *,
    target: bool = False,
    action_type: str = "slack",
) -> dict[str, Any]:
    return {
        "id": action_id,
        "type": "sentry_app" if target else action_type,
        "integrationId": None if target else "808",
        "data": (
            {"settings": [{"name": "alert_source_config", "value": "synthetic"}]}
            if target
            else {"tags": "", "notes": ""}
        ),
        "config": (
            {
                "targetType": "sentry_app",
                "targetDisplay": "incident.io",
                "targetIdentifier": "909",
            }
            if target
            else {
                "targetType": "specific",
                "targetDisplay": "#synthetic-alerts",
                "targetIdentifier": "606",
            }
        ),
        "status": "active",
    }


def _group(
    group_id: str,
    *,
    conditions: list[dict[str, Any]] | None = None,
    actions: list[dict[str, Any]] | None = None,
    logic_type: str = "all",
) -> dict[str, Any]:
    return {
        "id": group_id,
        "organizationId": "202",
        "logicType": logic_type,
        "conditions": conditions or [],
        "actions": actions or [],
    }


def _workflow(
    *,
    boundary: bool = False,
    target_logic: str = "all",
    target_actions: list[dict[str, Any]] | None = None,
    enabled: bool = True,
    environment: str | None = "production",
) -> dict[str, Any]:
    target_conditions = [
        _condition(
            "506",
            condition_type="tagged_event",
            comparison={
                "key": "service",
                "match": "eq",
                "value": "synthetic",
            },
        ),
    ]
    if boundary:
        target_conditions.append(
            _condition(
                "507",
                condition_type="tagged_event",
                comparison={
                    "key": ACTIVITY_BOUNDARY_KEY,
                    "match": "ne",
                    "value": ACTIVITY_BOUNDARY_VALUE,
                },
            )
        )
    return {
        "id": "101",
        "name": "Synthetic activity paging",
        "organizationId": "202",
        "createdBy": "203",
        "dateCreated": "2026-09-01T00:00:00Z",
        "dateUpdated": "2026-09-01T00:00:00Z",
        "triggers": _group(
            "303",
            conditions=[_condition("304")],
            actions=[],
            logic_type="any-short",
        ),
        "actionFilters": [
            _group(
                "404",
                conditions=target_conditions,
                actions=target_actions or [_action("505", target=True)],
                logic_type=target_logic,
            ),
            _group(
                "405",
                conditions=[
                    _condition(
                        "508",
                        condition_type="tagged_event",
                        comparison={
                            "key": "service",
                            "match": "eq",
                            "value": "synthetic",
                        },
                    )
                ],
                actions=[_action("509")],
                logic_type="all",
            ),
        ],
        "environment": environment,
        "config": {"frequency": 1440, "syntheticConfig": {"keep": True}},
        "detectorIds": ["707"],
        "enabled": enabled,
        "lastTriggered": None,
        "owner": None,
    }


def _reconcile_kwargs(**overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "base_url": "https://sentry.synthetic.example",
        "organization": "synthetic-org",
        "workflow_id": "101",
        "expected_environment": "production",
        "expected_name": "Synthetic activity paging",
        "expected_organization_id": "202",
        "expected_detector_ids": ["707"],
    }
    kwargs.update(overrides)
    return kwargs


def _updated_workflow(workflow: Mapping[str, Any]) -> dict[str, Any]:
    updated = copy.deepcopy(dict(workflow))
    updated["dateUpdated"] = "2026-09-13T00:00:00Z"
    updated["actionFilters"][0]["conditions"].append(
        _condition(
            "510",
            condition_type="tagged_event",
            comparison={
                "key": ACTIVITY_BOUNDARY_KEY,
                "match": "ne",
                "value": ACTIVITY_BOUNDARY_VALUE,
            },
        )
    )
    return updated


def test_dry_run_is_get_only_and_plan_excludes_action_settings() -> None:
    workflow = _workflow()
    transport = FakeTransport([FakeResponse(workflow)])

    result = reconcile(**_reconcile_kwargs(transport=transport))

    assert result.result == "would-update"
    assert [method for method, *_ in transport.calls] == ["GET"]
    document = result.public_document("dry-run")
    rendered = json.dumps(document)
    assert document["snapshot_sha256"] == snapshot_digest(workflow)
    assert "targetIdentifier" not in rendered
    assert "incident.io" not in rendered
    assert "synthetic-target" not in rendered
    assert "alert_source_config" not in rendered
    assert document["delta"]["appended_condition"] == {
        "type": "tagged_event",
        "comparison": {
            "key": ACTIVITY_BOUNDARY_KEY,
            "match": "ne",
            "value": ACTIVITY_BOUNDARY_VALUE,
        },
        "conditionResult": True,
    }
    assert result.plan.payload["actionFilters"][0]["conditions"][-1] == {
        "type": "tagged_event",
        "comparison": {
            "key": ACTIVITY_BOUNDARY_KEY,
            "match": "ne",
            "value": ACTIVITY_BOUNDARY_VALUE,
        },
        "conditionResult": True,
    }


def test_apply_requires_explicit_flag_and_reviewed_digest() -> None:
    workflow = _workflow()
    dry_run_transport = FakeTransport([FakeResponse(workflow)])
    dry_run = reconcile(**_reconcile_kwargs(transport=dry_run_transport))
    assert [method for method, *_ in dry_run_transport.calls] == ["GET"]

    with pytest.raises(ReconciliationError, match="reviewed dry-run digest"):
        reconcile(
            **_reconcile_kwargs(
                transport=FakeTransport([FakeResponse(workflow)]), apply=True
            )
        )

    updated = _updated_workflow(workflow)
    transport = FakeTransport(
        [
            FakeResponse(workflow),
            FakeResponse(workflow),
            FakeResponse(updated),
            FakeResponse(updated),
        ]
    )
    result = reconcile(
        **_reconcile_kwargs(
            transport=transport,
            apply=True,
            expected_digest=dry_run.plan.source_digest,
        )
    )
    assert result.result == "applied"
    assert [method for method, *_ in transport.calls] == ["GET", "GET", "PUT", "GET"]
    put_body = transport.calls[2][3]
    assert put_body is not None
    payload = json.loads(put_body)
    assert payload["actionFilters"][0]["conditions"][-1]["type"] == "tagged_event"
    assert "organizationId" not in payload
    assert "dateUpdated" not in payload


def test_repeat_is_noop_and_does_not_write() -> None:
    workflow = _workflow(boundary=True)
    transport = FakeTransport([FakeResponse(workflow)])

    result = reconcile(
        **_reconcile_kwargs(
            transport=transport,
            apply=True,
            expected_digest=snapshot_digest(workflow),
        )
    )

    assert result.result == "no-op"
    assert [method for method, *_ in transport.calls] == ["GET"]


def test_repeat_after_success_is_noop_with_original_digest() -> None:
    workflow = _workflow()
    updated = _updated_workflow(workflow)
    first_transport = FakeTransport(
        [
            FakeResponse(workflow),
            FakeResponse(workflow),
            FakeResponse(updated),
            FakeResponse(updated),
        ]
    )
    first = reconcile(
        **_reconcile_kwargs(
            transport=first_transport,
            apply=True,
            expected_digest=snapshot_digest(workflow),
        )
    )
    second_transport = FakeTransport([FakeResponse(updated)])

    second = reconcile(
        **_reconcile_kwargs(
            transport=second_transport,
            apply=True,
            expected_digest=first.plan.source_digest,
        )
    )

    assert first.result == "applied"
    assert second.result == "no-op"
    assert [method for method, *_ in second_transport.calls] == ["GET"]


def test_apply_refuses_drift_before_put() -> None:
    workflow = _workflow()
    drifted = copy.deepcopy(workflow)
    drifted["config"]["frequency"] = 60
    transport = FakeTransport([FakeResponse(workflow), FakeResponse(drifted)])

    with pytest.raises(DriftRefused):
        reconcile(
            **_reconcile_kwargs(
                transport=transport,
                apply=True,
                expected_digest=snapshot_digest(workflow),
            )
        )
    assert [method for method, *_ in transport.calls] == ["GET", "GET"]


def test_legacy_or_unexpected_shape_is_refused() -> None:
    legacy = {"id": "101", "name": "legacy", "rules": []}
    transport = FakeTransport([FakeResponse(legacy)])

    with pytest.raises(ReconciliationError, match="unexpected field shape"):
        reconcile(**_reconcile_kwargs(transport=transport))
    assert [method for method, *_ in transport.calls] == ["GET"]


def test_unrelated_filters_and_protected_metadata_are_preserved() -> None:
    workflow = _workflow()
    transport = FakeTransport([FakeResponse(workflow)])
    result = reconcile(**_reconcile_kwargs(transport=transport))

    payload = result.plan.payload
    assert set(payload) == {"name", "enabled", "actionFilters"}
    assert payload["name"] == workflow["name"]
    assert payload["enabled"] is True
    assert payload["actionFilters"][1] == {
        key: workflow["actionFilters"][1][key]
        for key in ("id", "logicType", "conditions", "actions")
    }


def test_target_and_terminal_event_semantics_are_explicit() -> None:
    workflow = _workflow()
    transport = FakeTransport([FakeResponse(workflow)])
    result = reconcile(**_reconcile_kwargs(transport=transport))
    conditions = result.plan.payload["actionFilters"][0]["conditions"]

    # Pinned Sentry semantics: tagged_event only evaluates GroupEvent tags and
    # NOT_EQUAL is true when the requested value is absent.  This covers both
    # the activity source tag and terminal captures that omit the tag.
    # Source: getsentry/sentry@e917c50c2413cd5082524c3f98791eb035fd38de,
    # workflow_engine/handlers/condition/tagged_event_handler.py and rules/match.py.
    def matches(tags: list[tuple[str, str]]) -> bool:
        for condition in conditions:
            comparison = condition["comparison"]
            values = [
                value.casefold()
                for key, value in tags
                if key.casefold() == comparison["key"].casefold()
            ]
            if (
                comparison["match"] == "eq"
                and comparison["value"].casefold() not in values
            ):
                return False
            if comparison["match"] == "ne" and comparison["value"].casefold() in values:
                return False
        return True

    assert (
        matches(
            [("service", "synthetic"), (ACTIVITY_BOUNDARY_KEY, ACTIVITY_BOUNDARY_VALUE)]
        )
        is False
    )
    assert (
        matches([("service", "synthetic"), (ACTIVITY_BOUNDARY_KEY, "workflow")]) is True
    )
    assert matches([("service", "synthetic")]) is True
    assert matches([]) is False


@pytest.mark.parametrize(
    ("workflow_kwargs", "error_match"),
    [
        ({"target_logic": "any"}, "ALL logic"),
        ({"target_logic": "none"}, "ALL logic"),
        (
            {"target_actions": [_action("511", target=True), _action("512")]},
            "mixed actions",
        ),
        (
            {
                "target_actions": [
                    _action("513", target=True),
                    _action("514", target=True),
                ]
            },
            "exactly one",
        ),
        ({"enabled": False}, "disabled"),
        ({"environment": "staging"}, "environment"),
    ],
)
def test_unsafe_target_shapes_are_refused(
    workflow_kwargs: dict[str, Any], error_match: str
) -> None:
    workflow = _workflow(**workflow_kwargs)
    transport = FakeTransport([FakeResponse(workflow)])

    with pytest.raises(ReconciliationError, match=error_match):
        reconcile(**_reconcile_kwargs(transport=transport))


def test_conflicting_boundary_filter_is_refused() -> None:
    workflow = _workflow()
    workflow["actionFilters"][1]["conditions"].append(
        _condition(
            "515",
            condition_type="tagged_event",
            comparison={
                "key": ACTIVITY_BOUNDARY_KEY,
                "match": "eq",
                "value": ACTIVITY_BOUNDARY_VALUE,
            },
        )
    )
    transport = FakeTransport([FakeResponse(workflow)])

    with pytest.raises(ReconciliationError, match="conflicting activity filter"):
        reconcile(**_reconcile_kwargs(transport=transport))


def test_unexpected_target_configuration_is_refused() -> None:
    target = _action("516", target=True)
    target["config"]["targetIdentifier"] = "opaque-target"
    workflow = _workflow(target_actions=[target])
    transport = FakeTransport([FakeResponse(workflow)])

    with pytest.raises(ReconciliationError, match="unexpected configuration"):
        reconcile(**_reconcile_kwargs(transport=transport))


def test_disabled_target_action_is_refused() -> None:
    target = _action("517", target=True)
    target["status"] = "disabled"
    workflow = _workflow(target_actions=[target])
    transport = FakeTransport([FakeResponse(workflow)])

    with pytest.raises(ReconciliationError, match="not active"):
        reconcile(**_reconcile_kwargs(transport=transport))


def test_url_validation_and_path_escaping() -> None:
    endpoint = workflow_endpoint(
        "https://sentry.synthetic.example/prefix/",
        "org/with space",
        "workflow/with space",
    )
    assert endpoint == (
        "https://sentry.synthetic.example/prefix/api/0/organizations/"
        "org%2Fwith%20space/workflows/workflow%2Fwith%20space/"
    )
    with pytest.raises(ReconciliationError, match="HTTPS"):
        workflow_endpoint("http://sentry.synthetic.example", "org", "workflow")
    with pytest.raises(ReconciliationError, match="credentials"):
        workflow_endpoint(
            "https://user:secret@sentry.synthetic.example", "org", "workflow"
        )
    with pytest.raises(ReconciliationError, match="query"):
        workflow_endpoint(
            "https://sentry.synthetic.example?token=secret", "org", "workflow"
        )


def test_ambiguous_put_is_not_retried() -> None:
    workflow = _workflow()

    class FailingPutTransport(FakeTransport):
        def request(
            self,
            method: str,
            url: str,
            headers: Mapping[str, str],
            body: bytes | None = None,
        ) -> HttpResponse:
            if method == "PUT":
                self.calls.append((method, url, headers, body))
                raise ReconciliationError(
                    "transport_error", "synthetic transport failure"
                )
            return super().request(method, url, headers, body)

    transport = FailingPutTransport([FakeResponse(workflow), FakeResponse(workflow)])
    with pytest.raises(AmbiguousMutation):
        reconcile(
            **_reconcile_kwargs(
                transport=transport,
                apply=True,
                expected_digest=snapshot_digest(workflow),
            )
        )
    assert [method for method, *_ in transport.calls] == ["GET", "GET", "PUT"]


def test_post_write_filter_removal_is_a_controlled_verification_failure() -> None:
    workflow = _workflow()
    updated = _updated_workflow(workflow)
    updated["actionFilters"].pop()
    transport = FakeTransport(
        [
            FakeResponse(workflow),
            FakeResponse(workflow),
            FakeResponse(_updated_workflow(workflow)),
            FakeResponse(updated),
        ]
    )

    with pytest.raises(ReconciliationError, match="number of action filters"):
        reconcile(
            **_reconcile_kwargs(
                transport=transport,
                apply=True,
                expected_digest=snapshot_digest(workflow),
            )
        )
    assert [method for method, *_ in transport.calls] == ["GET", "GET", "PUT", "GET"]
