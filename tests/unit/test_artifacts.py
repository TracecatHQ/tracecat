from __future__ import annotations

from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any

import pytest

from tracecat.artifacts.bindings import (
    ARTIFACT_BINDINGS,
    MAX_LIST_ARTIFACTS,
    ArtifactIdentityRef,
    ArtifactSideEffect,
    artifact_side_effects_for_tool_result,
)
from tracecat.artifacts.projection import (
    MAX_OPEN_ARTIFACTS,
    apply_artifact_side_effects,
    remove_artifact,
    serialize_artifacts,
    validate_artifacts,
)
from tracecat.artifacts.schemas import (
    CaseArtifact,
    artifact_data_payload,
)
from tracecat.cases.enums import CaseSeverity, CaseStatus


def test_artifact_projection_applies_upsert_and_remove_operations() -> None:
    first = CaseArtifact(
        id="case_1",
        title="Initial title",
        severity=CaseSeverity.LOW,
        status=CaseStatus.NEW,
    )
    updated = CaseArtifact(
        id="case_1",
        title="Updated title",
        severity=CaseSeverity.HIGH,
        status=CaseStatus.IN_PROGRESS,
    )
    second = CaseArtifact(
        id="case_2",
        title="Second case",
        severity=CaseSeverity.MEDIUM,
        status=CaseStatus.NEW,
    )

    projected = apply_artifact_side_effects(
        [first],
        [
            ArtifactSideEffect(op="upsert", artifact=updated),
            ArtifactSideEffect(op="upsert", artifact=second),
            ArtifactSideEffect(op="remove", artifact=updated),
        ],
    )

    assert projected == [second]


def test_artifact_projection_evicts_oldest_past_open_limit() -> None:
    def case(i: int) -> CaseArtifact:
        return CaseArtifact(
            id=f"case_{i}",
            title=f"Case {i}",
            severity=CaseSeverity.LOW,
            status=CaseStatus.NEW,
        )

    total = MAX_OPEN_ARTIFACTS + 2
    projected = apply_artifact_side_effects(
        [],
        [ArtifactSideEffect(op="upsert", artifact=case(i)) for i in range(total)],
    )

    assert [artifact.id for artifact in projected] == [
        f"case_{i}" for i in range(total - MAX_OPEN_ARTIFACTS, total)
    ]


def test_artifact_projection_upsert_refreshes_recency() -> None:
    def case(i: int) -> CaseArtifact:
        return CaseArtifact(
            id=f"case_{i}",
            title=f"Case {i}",
            severity=CaseSeverity.LOW,
            status=CaseStatus.NEW,
        )

    initial = [case(i) for i in range(MAX_OPEN_ARTIFACTS)]
    projected = apply_artifact_side_effects(
        initial,
        [
            # Touch the oldest artifact, then add one more: the second-oldest
            # should be evicted instead of the refreshed one.
            ArtifactSideEffect(op="upsert", artifact=case(0)),
            ArtifactSideEffect(op="upsert", artifact=case(MAX_OPEN_ARTIFACTS)),
        ],
    )

    assert [artifact.id for artifact in projected] == [
        f"case_{i}" for i in range(2, MAX_OPEN_ARTIFACTS)
    ] + ["case_0", f"case_{MAX_OPEN_ARTIFACTS}"]


def test_artifact_projection_serializes_and_validates_jsonb_payload() -> None:
    artifact = CaseArtifact(
        id="case_1",
        title="Suspicious login",
        severity=CaseSeverity.HIGH,
        status=CaseStatus.NEW,
    )

    serialized = serialize_artifacts([artifact])

    assert serialized == [
        {
            "type": "case",
            "id": "case_1",
            "title": "Suspicious login",
            "severity": "high",
            "status": "new",
        }
    ]
    assert validate_artifacts(serialized) == [artifact]


def test_remove_artifact_filters_by_type_and_id() -> None:
    artifact = CaseArtifact(
        id="case_1",
        title="Suspicious login",
        severity=CaseSeverity.HIGH,
        status=CaseStatus.NEW,
    )

    assert remove_artifact([artifact], artifact_type="case", artifact_id="case_1") == []


def test_artifact_bindings_list_canonical_tool_names() -> None:
    tool_names = [
        tool_name for binding in ARTIFACT_BINDINGS for tool_name in binding.tool_names
    ]

    assert len(tool_names) == len(set(tool_names))
    assert {
        tool_name: binding.op
        for binding in ARTIFACT_BINDINGS
        for tool_name in binding.tool_names
    } == {
        "core.cases.create_case": "upsert",
        "core.cases.update_case": "upsert",
        "core.cases.get_case": "upsert",
        "core.cases.list_cases": "upsert",
        "core.cases.search_cases": "upsert",
        "core.cases.delete_case": "remove",
        "core.table.create_table": "upsert",
        "core.table.list_tables": "upsert",
        "core.table.get_table_metadata": "upsert",
        "core.table.update_table": "upsert",
        "core.table.create_column": "upsert",
        "core.table.update_column": "upsert",
        "core.table.delete_column": "upsert",
        "core.table.lookup": "upsert",
        "core.table.lookup_many": "upsert",
        "core.table.is_in": "upsert",
        "core.table.search_rows": "upsert",
        "core.table.aggregate_rows": "upsert",
        "core.table.insert_row": "upsert",
        "core.table.insert_rows": "upsert",
        "core.table.update_row": "upsert",
        "core.table.delete_row": "upsert",
        "core.table.download": "upsert",
        "ai.agent.create_preset": "upsert",
        "ai.agent.get_preset": "upsert",
        "ai.agent.list_presets": "upsert",
        "ai.agent.update_preset": "upsert",
        "core.forms.get_form": "upsert",
        "core.forms.update_form": "upsert",
        "core.workflow.execute": "upsert",
        "core.workflow.get_status": "upsert",
        "core.workflow.create_workflow": "upsert",
        "core.workflow.get_workflow": "upsert",
        "core.workflow.edit_workflow": "upsert",
    }


_SCOPE = {"parentToolCallId": "toolu_123"}
_TABLE_INDICATORS = {
    "type": "table",
    "id": "table_123",
    "title": "indicators",
    "scope": _SCOPE,
}
_CASE_SUSPICIOUS_LOGIN = {
    "type": "case",
    "id": "case_123",
    "title": "Suspicious login",
    "scope": _SCOPE,
    "severity": "high",
    "status": "new",
}
_TRIAGE_WORKFLOW = {
    "type": "workflow",
    "id": "wf-1234",
    "title": "Triage workflow",
    "scope": _SCOPE,
    "color": "#6E56CF",
}


def _table_by_name(name: str) -> dict[str, object]:
    return {"type": "table", "id": name, "title": name, "scope": _SCOPE}


def _name_ref(name: str) -> ArtifactIdentityRef:
    return ArtifactIdentityRef(artifact_type="table", ref=name, ref_kind="name")


@pytest.mark.parametrize(
    ("tool_name", "tool_input", "tool_output", "expected", "identity_ref"),
    [
        pytest.param(
            "core.cases.create_case",
            {"summary": "Suspicious login"},
            {
                "id": "case_123",
                "summary": "Suspicious login",
                "severity": "high",
                "status": "new",
            },
            {"op": "upsert", "artifact": _CASE_SUSPICIOUS_LOGIN},
            None,
            id="case-create",
        ),
        pytest.param(
            "core.cases.create_case",
            {"summary": "Suspicious login"},
            [
                SimpleNamespace(
                    type="text",
                    text=(
                        '{"id":"case_123","summary":"Suspicious login",'
                        '"severity":"high","status":"new"}'
                    ),
                )
            ],
            {"op": "upsert", "artifact": _CASE_SUSPICIOUS_LOGIN},
            None,
            id="case-create-sdk-text-block",
        ),
        pytest.param(
            "core.cases.delete_case",
            {"case_id": "case_123"},
            None,
            {
                "op": "remove",
                "artifact": {
                    "type": "case",
                    "id": "case_123",
                    "title": "case_123",
                    "scope": _SCOPE,
                    "severity": "unknown",
                    "status": "unknown",
                },
            },
            None,
            id="case-delete-removes",
        ),
        pytest.param(
            "custom.tool",
            None,
            {
                "op": "remove",
                "artifact": {
                    "type": "generic",
                    "id": "artifact_123",
                    "title": "Artifact 123",
                },
            },
            {
                "op": "remove",
                "artifact": {
                    "type": "generic",
                    "id": "artifact_123",
                    "title": "Artifact 123",
                    "scope": _SCOPE,
                },
            },
            None,
            id="explicit-wrapper-preserves-remove",
        ),
        pytest.param(
            "core.table.create_table",
            {"name": "indicators"},
            [
                {
                    "type": "text",
                    "text": (
                        '{"id":"table_123","name":"indicators",'
                        '"workspace_id":"workspace_123"}'
                    ),
                }
            ],
            {"op": "upsert", "artifact": _TABLE_INDICATORS},
            None,
            id="table-create-content-block",
        ),
        pytest.param(
            "core.table.create_table",
            {"name": "indicators"},
            {
                "content": {
                    "id": "table_123",
                    "name": "indicators",
                    "workspace_id": "workspace_123",
                }
            },
            {"op": "upsert", "artifact": _TABLE_INDICATORS},
            None,
            id="table-create-mapping-content",
        ),
        pytest.param(
            "core.table.update_column",
            {"table": "indicators", "column": "score"},
            {
                "id": "table_123",
                "name": "indicators",
                "columns": [{"name": "score", "type": "NUMERIC"}],
            },
            {"op": "upsert", "artifact": _TABLE_INDICATORS},
            None,
            id="table-schema-update",
        ),
        pytest.param(
            "core.table.aggregate_rows",
            {"table": "alerts", "group_by": ["source"]},
            {"groups": [{"source": "sensor", "count": 3}], "truncated": False},
            {"op": "upsert", "artifact": _table_by_name("alerts")},
            _name_ref("alerts"),
            id="table-aggregate-references-input",
        ),
        pytest.param(
            "core.table.delete_row",
            {"table": "table_123", "row_id": "row_123"},
            None,
            {"op": "upsert", "artifact": _table_by_name("table_123")},
            _name_ref("table_123"),
            id="table-row-delete-from-input",
        ),
        pytest.param(
            "core.table.search_rows",
            {"table": "indicators", "search_term": "evil.com"},
            {"items": [{"id": "row_123", "domain": "evil.com"}], "has_more": False},
            {"op": "upsert", "artifact": _table_by_name("indicators")},
            _name_ref("indicators"),
            id="table-search-from-input",
        ),
        pytest.param(
            "core.table.download",
            {"name": "indicators", "format": "json"},
            [{"domain": "evil.com"}],
            {"op": "upsert", "artifact": _table_by_name("indicators")},
            _name_ref("indicators"),
            id="table-download-from-name-input",
        ),
        pytest.param(
            "core.table.insert_rows",
            {"table_id": "11111111-1111-4111-8111-111111111111"},
            2,
            {
                "op": "upsert",
                "artifact": _table_by_name("11111111-1111-4111-8111-111111111111"),
            },
            ArtifactIdentityRef(
                artifact_type="table",
                ref="11111111-1111-4111-8111-111111111111",
                ref_kind="id",
            ),
            id="table-id-alias",
        ),
        pytest.param(
            "ai.agent.create_preset",
            {"name": "Case Triage"},
            {"id": "preset_123", "name": "Case Triage", "slug": "case-triage"},
            {
                "op": "upsert",
                "artifact": {
                    "type": "agent",
                    "id": "preset_123",
                    "title": "Case Triage",
                    "scope": _SCOPE,
                },
            },
            None,
            id="agent-preset-create",
        ),
        pytest.param(
            "core.workflow.create_workflow",
            {"title": "Triage workflow"},
            {"id": "wf-1234", "title": "Triage workflow"},
            {"op": "upsert", "artifact": _TRIAGE_WORKFLOW},
            None,
            id="workflow-create",
        ),
        pytest.param(
            "core.workflow.get_workflow",
            None,
            {
                "id": "wf-1234",
                "draft_document": {"metadata": {"title": "Triage workflow"}},
            },
            {"op": "upsert", "artifact": _TRIAGE_WORKFLOW},
            None,
            id="workflow-get-nested-draft-title",
        ),
    ],
)
def test_tool_result_emits_single_side_effect(
    tool_name: str,
    tool_input: Mapping[str, Any] | None,
    tool_output: object,
    expected: dict[str, object],
    identity_ref: ArtifactIdentityRef | None,
) -> None:
    [effect] = artifact_side_effects_for_tool_result(
        tool_name=tool_name,
        tool_input=tool_input,
        tool_output=tool_output,
        is_error=False,
        tool_call_id="toolu_123",
    )

    assert artifact_data_payload(effect.op, effect.artifact) == expected
    assert effect.identity_ref == identity_ref


@pytest.mark.parametrize(
    ("tool_name", "tool_input", "tool_output", "expected_ids"),
    [
        pytest.param(
            "core.cases.list_cases",
            {"limit": 2},
            {
                "items": [
                    {
                        "id": "case_123",
                        "summary": "Suspicious login",
                        "severity": "high",
                        "status": "new",
                    },
                    {
                        "id": "case_456",
                        "summary": "Malware alert",
                        "severity": "critical",
                        "status": "in_progress",
                    },
                ],
                "has_more": False,
            },
            ["case_123", "case_456"],
            id="cases",
        ),
        pytest.param(
            "core.table.list_tables",
            None,
            [
                {"id": "table_123", "name": "indicators"},
                {"id": "table_456", "name": "alerts"},
            ],
            ["table_123", "table_456"],
            id="tables",
        ),
        pytest.param(
            "ai.agent.list_presets",
            None,
            [
                {"id": "preset_123", "name": "Case Triage"},
                {"id": "preset_456", "name": "Table Builder"},
            ],
            ["preset_123", "preset_456"],
            id="agent-presets",
        ),
    ],
)
def test_list_tool_result_emits_upsert_side_effects(
    tool_name: str,
    tool_input: Mapping[str, Any] | None,
    tool_output: object,
    expected_ids: list[str],
) -> None:
    effects = list(
        artifact_side_effects_for_tool_result(
            tool_name=tool_name,
            tool_input=tool_input,
            tool_output=tool_output,
            is_error=False,
            tool_call_id="toolu_123",
        )
    )

    assert [effect.op for effect in effects] == ["upsert"] * len(expected_ids)
    assert [effect.artifact.id for effect in effects] == expected_ids


@pytest.mark.parametrize(
    ("tool_name", "tool_input", "tool_output", "is_error"),
    [
        pytest.param(
            "core.cases.list_cases",
            {"limit": 10},
            {
                "items": [
                    {
                        "id": f"case_{i}",
                        "summary": f"Case {i}",
                        "severity": "low",
                        "status": "new",
                    }
                    for i in range(MAX_LIST_ARTIFACTS + 1)
                ],
                "has_more": False,
            },
            False,
            id="case-list-above-limit",
        ),
        pytest.param(
            "core.table.list_tables",
            None,
            [
                {"id": f"table_{i}", "name": f"table-{i}"}
                for i in range(MAX_LIST_ARTIFACTS + 1)
            ],
            False,
            id="table-list-above-limit",
        ),
        pytest.param(
            "ai.agent.list_presets",
            None,
            [
                {"id": f"preset_{i}", "name": f"Preset {i}"}
                for i in range(MAX_LIST_ARTIFACTS + 1)
            ],
            False,
            id="agent-preset-list-above-limit",
        ),
        pytest.param(
            "core.cases.aggregate_cases",
            {"group_by": ["priority"]},
            {"groups": [{"priority": "high", "count": 3}], "truncated": False},
            False,
            id="case-aggregate-groups-are-not-cases",
        ),
        # edit_workflow returns no title, so it must not upsert and clobber the
        # workflow artifact title (set by create/get) with the workflow id.
        pytest.param(
            "core.workflow.edit_workflow",
            {"workflow_id": "wf-1234"},
            {
                "message": "Workflow wf-1234 updated successfully",
                "workflow_id": "wf-1234",
                "draft_revision": "rev-1",
            },
            False,
            id="edit-workflow-titleless",
        ),
        pytest.param(
            "core.cases.create_case",
            {"summary": "Suspicious login"},
            {"id": "case_123", "summary": "Suspicious login"},
            True,
            id="error-result",
        ),
    ],
)
def test_tool_result_emits_no_side_effects(
    tool_name: str,
    tool_input: Mapping[str, Any] | None,
    tool_output: object,
    is_error: bool,
) -> None:
    effects = list(
        artifact_side_effects_for_tool_result(
            tool_name=tool_name,
            tool_input=tool_input,
            tool_output=tool_output,
            is_error=is_error,
            tool_call_id="toolu_123",
        )
    )

    assert effects == []


@pytest.mark.parametrize(
    ("tool_name", "config", "title"),
    [
        (
            "core.forms.get_form",
            {"title": "Laptop request", "layout": []},
            "Laptop request",
        ),
        ("core.forms.update_form", {}, "Provision laptop"),
    ],
    ids=["config-title", "workflow-title-fallback"],
)
def test_form_tool_result_emits_upsert_side_effect(
    tool_name: str, config: dict[str, object], title: str
) -> None:
    [effect] = artifact_side_effects_for_tool_result(
        tool_name=tool_name,
        tool_input={"workflow_id": "wf_123"},
        tool_output={
            "id": "0b0c8f8e-0d0b-4d3a-9a57-3d0f2a3e6b11",
            "workflow_id": "wf_123",
            "workflow_title": "Provision laptop",
            "status": "online",
            "config": config,
            "issues": [],
        },
        is_error=False,
        tool_call_id="toolu_123",
    )

    assert artifact_data_payload(effect.op, effect.artifact) == {
        "op": "upsert",
        "artifact": {
            "type": "form",
            "id": "0b0c8f8e-0d0b-4d3a-9a57-3d0f2a3e6b11",
            "title": title,
            "status": "online",
            "scope": {"parentToolCallId": "toolu_123"},
        },
    }
