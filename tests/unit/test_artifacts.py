from __future__ import annotations

from types import SimpleNamespace

from tracecat.artifacts.bindings import (
    ARTIFACT_BINDINGS,
    ArtifactIdentityRef,
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
from tracecat.chat.tools import WORKSPACE_CHAT_DEFAULT_TOOLS


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
        "core.table.insert_row": "upsert",
        "core.table.insert_rows": "upsert",
        "core.table.update_row": "upsert",
        "core.table.delete_row": "upsert",
        "core.table.download": "upsert",
        "ai.agent.create_preset": "upsert",
        "ai.agent.get_preset": "upsert",
        "ai.agent.list_presets": "upsert",
        "ai.agent.update_preset": "upsert",
        "core.workflow.execute": "upsert",
        "core.workflow.get_status": "upsert",
        "core.workflow.create_workflow": "upsert",
    }


def test_workspace_chat_domain_tools_have_artifact_bindings() -> None:
    bound_tool_names = {
        tool_name for binding in ARTIFACT_BINDINGS for tool_name in binding.tool_names
    }

    assert set(WORKSPACE_CHAT_DEFAULT_TOOLS).issubset(bound_tool_names)


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
    assert effects[0].identity_ref is None


def test_table_schema_tool_result_emits_upsert_side_effect() -> None:
    effects = list(
        artifact_side_effects_for_tool_result(
            tool_name="core.table.update_column",
            tool_input={"table": "indicators", "column": "score"},
            tool_output={
                "id": "table_123",
                "name": "indicators",
                "columns": [{"name": "score", "type": "NUMERIC"}],
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
    assert effects[0].identity_ref is None


def test_table_row_delete_tool_result_emits_upsert_side_effect_from_input() -> None:
    effects = list(
        artifact_side_effects_for_tool_result(
            tool_name="core.table.delete_row",
            tool_input={"table": "table_123", "row_id": "row_123"},
            tool_output=None,
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
            "title": "table_123",
            "scope": {"parentToolCallId": "toolu_123"},
        },
    }
    assert effects[0].identity_ref == ArtifactIdentityRef(
        artifact_type="table",
        ref="table_123",
        ref_kind="name",
    )


def test_table_input_artifact_identity_supports_table_id_alias() -> None:
    effects = list(
        artifact_side_effects_for_tool_result(
            tool_name="core.table.insert_rows",
            tool_input={"table_id": "11111111-1111-4111-8111-111111111111"},
            tool_output=2,
            is_error=False,
            tool_call_id="toolu_123",
        )
    )

    assert len(effects) == 1
    assert effects[0].identity_ref == ArtifactIdentityRef(
        artifact_type="table",
        ref="11111111-1111-4111-8111-111111111111",
        ref_kind="id",
    )


def test_case_list_tool_result_emits_upsert_side_effects() -> None:
    effects = list(
        artifact_side_effects_for_tool_result(
            tool_name="core.cases.list_cases",
            tool_input={"limit": 2},
            tool_output={
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
            is_error=False,
            tool_call_id="toolu_123",
        )
    )

    assert [effect.op for effect in effects] == ["upsert", "upsert"]
    assert [effect.artifact.id for effect in effects] == ["case_123", "case_456"]


def test_table_list_tool_result_emits_upsert_side_effects() -> None:
    effects = list(
        artifact_side_effects_for_tool_result(
            tool_name="core.table.list_tables",
            tool_input=None,
            tool_output=[
                {"id": "table_123", "name": "indicators"},
                {"id": "table_456", "name": "alerts"},
            ],
            is_error=False,
            tool_call_id="toolu_123",
        )
    )

    assert [effect.op for effect in effects] == ["upsert", "upsert"]
    assert [effect.artifact.id for effect in effects] == ["table_123", "table_456"]


def test_table_search_tool_result_emits_upsert_side_effect_from_input() -> None:
    effects = list(
        artifact_side_effects_for_tool_result(
            tool_name="core.table.search_rows",
            tool_input={"table": "indicators", "search_term": "evil.com"},
            tool_output={
                "items": [{"id": "row_123", "domain": "evil.com"}],
                "has_more": False,
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
            "id": "indicators",
            "title": "indicators",
            "scope": {"parentToolCallId": "toolu_123"},
        },
    }
    assert effects[0].identity_ref == ArtifactIdentityRef(
        artifact_type="table",
        ref="indicators",
        ref_kind="name",
    )


def test_table_download_tool_result_emits_upsert_side_effect_from_name_input() -> None:
    effects = list(
        artifact_side_effects_for_tool_result(
            tool_name="core.table.download",
            tool_input={"name": "indicators", "format": "json"},
            tool_output=[{"domain": "evil.com"}],
            is_error=False,
            tool_call_id="toolu_123",
        )
    )

    assert len(effects) == 1
    assert effects[0].artifact.id == "indicators"
    assert effects[0].identity_ref == ArtifactIdentityRef(
        artifact_type="table",
        ref="indicators",
        ref_kind="name",
    )


def test_agent_preset_tool_result_emits_upsert_side_effect() -> None:
    effects = list(
        artifact_side_effects_for_tool_result(
            tool_name="ai.agent.create_preset",
            tool_input={"name": "Case Triage"},
            tool_output={
                "id": "preset_123",
                "name": "Case Triage",
                "slug": "case-triage",
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
            "type": "agent",
            "id": "preset_123",
            "title": "Case Triage",
            "scope": {"parentToolCallId": "toolu_123"},
        },
    }


def test_agent_preset_list_tool_result_emits_upsert_side_effects() -> None:
    effects = list(
        artifact_side_effects_for_tool_result(
            tool_name="ai.agent.list_presets",
            tool_input=None,
            tool_output=[
                {"id": "preset_123", "name": "Case Triage"},
                {"id": "preset_456", "name": "Table Builder"},
            ],
            is_error=False,
            tool_call_id="toolu_123",
        )
    )

    assert [effect.op for effect in effects] == ["upsert", "upsert"]
    assert [effect.artifact.id for effect in effects] == [
        "preset_123",
        "preset_456",
    ]


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
