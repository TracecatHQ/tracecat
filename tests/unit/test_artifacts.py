from __future__ import annotations

from types import SimpleNamespace

from tracecat.artifacts.bindings import (
    ARTIFACT_BINDINGS,
    ArtifactSideEffect,
    artifact_side_effects_for_tool_result,
)
from tracecat.artifacts.projection import (
    apply_artifact_side_effects,
    remove_artifact,
    serialize_artifacts,
    validate_artifacts,
)
from tracecat.artifacts.schemas import (
    ArtifactAdapter,
    CaseArtifact,
    WorkflowArtifact,
    artifact_data_payload,
)
from tracecat.cases.enums import CaseSeverity, CaseStatus


def test_artifact_data_payload_serializes_camel_case_fields() -> None:
    artifact = ArtifactAdapter.validate_python(
        {
            "type": "workflow",
            "id": "wf_123",
            "title": "Triage workflow",
            "color": "#64748b",
            "isPublished": True,
        }
    )

    assert artifact_data_payload("upsert", artifact) == {
        "op": "upsert",
        "artifact": {
            "type": "workflow",
            "id": "wf_123",
            "title": "Triage workflow",
            "color": "#64748b",
            "isPublished": True,
        },
    }


def test_artifact_adapter_validates_aliases() -> None:
    artifact = ArtifactAdapter.validate_python(
        {
            "type": "workflow",
            "id": "wf_123",
            "title": "Triage workflow",
            "color": "#64748b",
            "isPublished": False,
        }
    )

    assert isinstance(artifact, WorkflowArtifact)
    assert artifact.is_published is False


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


def test_case_artifact_uses_case_domain_enums() -> None:
    artifact = CaseArtifact(
        id="case_123",
        title="Suspicious login",
        severity=CaseSeverity.HIGH,
        status=CaseStatus.NEW,
    )

    assert artifact_data_payload("upsert", artifact) == {
        "op": "upsert",
        "artifact": {
            "type": "case",
            "id": "case_123",
            "title": "Suspicious login",
            "severity": "high",
            "status": "new",
        },
    }


def test_case_tool_result_emits_upsert_side_effect() -> None:
    effects = list(
        artifact_side_effects_for_tool_result(
            tool_name="core.cases.create_case",
            tool_input={"summary": "Suspicious login"},
            tool_output={
                "id": "case_123",
                "summary": "Suspicious login",
                "severity": "high",
                "status": "new",
            },
            is_error=False,
            tool_call_id="toolu_123",
        )
    )

    assert len(effects) == 1
    assert effects[0].op == "upsert"
    payload = artifact_data_payload(effects[0].op, effects[0].artifact)
    assert payload == {
        "op": "upsert",
        "artifact": {
            "type": "case",
            "id": "case_123",
            "title": "Suspicious login",
            "scope": {"parentToolCallId": "toolu_123"},
            "severity": "high",
            "status": "new",
        },
    }


def test_explicit_artifact_wrapper_preserves_remove_operation() -> None:
    effects = list(
        artifact_side_effects_for_tool_result(
            tool_name="custom.tool",
            tool_input=None,
            tool_output={
                "op": "remove",
                "artifact": {
                    "type": "generic",
                    "id": "artifact_123",
                    "title": "Artifact 123",
                },
            },
            is_error=False,
            tool_call_id="toolu_123",
        )
    )

    assert len(effects) == 1
    assert effects[0].op == "remove"
    payload = artifact_data_payload(effects[0].op, effects[0].artifact)
    assert payload == {
        "op": "remove",
        "artifact": {
            "type": "generic",
            "id": "artifact_123",
            "title": "Artifact 123",
            "scope": {"parentToolCallId": "toolu_123"},
        },
    }


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
        "core.cases.delete_case": "remove",
        "core.table.create_table": "upsert",
        "core.workflow.execute": "upsert",
        "core.workflow.get_status": "upsert",
    }


def test_case_delete_tool_result_emits_remove_side_effect() -> None:
    effects = list(
        artifact_side_effects_for_tool_result(
            tool_name="core.cases.delete_case",
            tool_input={"case_id": "case_123"},
            tool_output=None,
            is_error=False,
            tool_call_id="toolu_123",
        )
    )

    assert len(effects) == 1
    assert effects[0].op == "remove"
    payload = artifact_data_payload(effects[0].op, effects[0].artifact)
    assert payload == {
        "op": "remove",
        "artifact": {
            "type": "case",
            "id": "case_123",
            "title": "case_123",
            "scope": {"parentToolCallId": "toolu_123"},
            "severity": "unknown",
            "status": "unknown",
        },
    }


def test_table_tool_result_content_block_emits_upsert_side_effect() -> None:
    effects = list(
        artifact_side_effects_for_tool_result(
            tool_name="core.table.create_table",
            tool_input={"name": "indicators"},
            tool_output=[
                {
                    "type": "text",
                    "text": (
                        '{"id":"table_123","name":"indicators",'
                        '"workspace_id":"workspace_123"}'
                    ),
                }
            ],
            is_error=False,
            tool_call_id="toolu_123",
        )
    )

    assert len(effects) == 1
    assert effects[0].op == "upsert"
    payload = artifact_data_payload(effects[0].op, effects[0].artifact)
    assert payload == {
        "op": "upsert",
        "artifact": {
            "type": "table",
            "id": "table_123",
            "title": "indicators",
            "scope": {"parentToolCallId": "toolu_123"},
        },
    }


def test_table_tool_result_mapping_content_emits_upsert_side_effect() -> None:
    effects = list(
        artifact_side_effects_for_tool_result(
            tool_name="core.table.create_table",
            tool_input={"name": "indicators"},
            tool_output={
                "content": {
                    "id": "table_123",
                    "name": "indicators",
                    "workspace_id": "workspace_123",
                }
            },
            is_error=False,
            tool_call_id="toolu_123",
        )
    )

    assert len(effects) == 1
    assert effects[0].op == "upsert"
    payload = artifact_data_payload(effects[0].op, effects[0].artifact)
    assert payload == {
        "op": "upsert",
        "artifact": {
            "type": "table",
            "id": "table_123",
            "title": "indicators",
            "scope": {"parentToolCallId": "toolu_123"},
        },
    }


def test_case_tool_result_sdk_text_block_emits_upsert_side_effect() -> None:
    effects = list(
        artifact_side_effects_for_tool_result(
            tool_name="core.cases.create_case",
            tool_input={"summary": "Suspicious login"},
            tool_output=[
                SimpleNamespace(
                    type="text",
                    text=(
                        '{"id":"case_123","summary":"Suspicious login",'
                        '"severity":"high","status":"new"}'
                    ),
                )
            ],
            is_error=False,
            tool_call_id="toolu_123",
        )
    )

    assert len(effects) == 1
    assert effects[0].op == "upsert"
    payload = artifact_data_payload(effects[0].op, effects[0].artifact)
    assert payload == {
        "op": "upsert",
        "artifact": {
            "type": "case",
            "id": "case_123",
            "title": "Suspicious login",
            "scope": {"parentToolCallId": "toolu_123"},
            "severity": "high",
            "status": "new",
        },
    }


def test_error_tool_result_does_not_emit_artifact_side_effects() -> None:
    effects = list(
        artifact_side_effects_for_tool_result(
            tool_name="core.cases.create_case",
            tool_input={"summary": "Suspicious login"},
            tool_output={"id": "case_123", "summary": "Suspicious login"},
            is_error=True,
            tool_call_id="toolu_123",
        )
    )

    assert effects == []
