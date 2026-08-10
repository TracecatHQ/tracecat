from typing import Any

import pytest

from tracecat.dsl.schemas import TemplateExecutionContext
from tracecat.exceptions import TracecatExpressionError
from tracecat.executor.expression_policy import (
    POLICY_MAP,
    ActionParameter,
    ExpressionPolicy,
    TemplateExecutionState,
    derive_secret_dependencies,
    derive_source_provenance,
    expression_policy,
    partition_action_args,
    prepare_action_args,
    redact_secret_expressions,
    substitute_source_references,
)
from tracecat.secrets.constants import MASK_VALUE


def _state(
    source_args: dict[str, Any],
    runtime_inputs: dict[str, Any],
    variables: dict[str, Any] | None = None,
) -> TemplateExecutionState:
    provenance = derive_source_provenance(source_args)
    context = TemplateExecutionContext(
        SECRETS={},
        VARS=variables or {},
        inputs=runtime_inputs,
        steps={},
    )
    return TemplateExecutionState(context, provenance)


def test_action_parameter_policy_scope_is_explicit() -> None:
    # PRESERVE skips evaluation entirely, so its scope must stay tiny and
    # explicitly reviewed. POLICY_MAP is the single source of truth for
    # REDACT_SECRETS scope; listing RESOLVE entries would be dead weight
    # since it is already the default.
    preserved = {
        parameter
        for parameter, policy in POLICY_MAP.items()
        if policy is ExpressionPolicy.PRESERVE
    }
    assert preserved == {
        ActionParameter(action="core.workflow.edit_workflow", parameter="patch_ops"),
        ActionParameter(
            action="core.workflow.create_workflow", parameter="definition_yaml"
        ),
    }
    assert all(
        policy is ExpressionPolicy.REDACT_SECRETS
        for parameter, policy in POLICY_MAP.items()
        if parameter not in preserved
    )


def test_expression_policy_requires_exact_action_parameter_pair() -> None:
    assert (
        expression_policy("core.workflow.edit_workflow", "patch_ops")
        is ExpressionPolicy.PRESERVE
    )
    assert (
        expression_policy("core.workflow.edit_workflow", "workflow_id")
        is ExpressionPolicy.RESOLVE
    )
    assert (
        expression_policy("core.transform.reshape", "patch_ops")
        is ExpressionPolicy.RESOLVE
    )


def test_redact_secret_expressions_preserves_non_secret_templates() -> None:
    value = (
        "Host: ${{ VARS.api.host }}, "
        "token: ${{ SECRETS.api.TOKEN }}, "
        "time: ${{ FN.now() }}, "
        "result: ${{ ACTIONS.lookup.result }}"
    )

    assert redact_secret_expressions(value) == (
        f"Host: ${{{{ VARS.api.host }}}}, token: {MASK_VALUE}, "
        "time: ${{ FN.now() }}, result: ${{ ACTIONS.lookup.result }}"
    )


def test_redact_secret_expressions_replaces_complex_occurrence() -> None:
    value = "${{ FN.to_base64(SECRETS.api.TOKEN + VARS.api.suffix) }}"

    assert redact_secret_expressions(value) == MASK_VALUE


def test_redact_secret_expressions_uses_ast_dependencies() -> None:
    value = (
        "Plain SECRETS.api.TOKEN; "
        "literal ${{ 'SECRETS.api.TOKEN' }}; "
        "reference ${{ SECRETS.api.TOKEN }}"
    )

    assert redact_secret_expressions(value) == (
        "Plain SECRETS.api.TOKEN; "
        "literal ${{ 'SECRETS.api.TOKEN' }}; "
        f"reference {MASK_VALUE}"
    )


def test_redact_secret_expressions_rejects_secret_dependent_keys() -> None:
    value = {
        "${{ SECRETS.api.KEY }}": 1,
        "${{ SECRETS.other.KEY }}": 2,
    }

    with pytest.raises(TracecatExpressionError) as exc_info:
        redact_secret_expressions(value)

    assert exc_info.value.detail == {"code": "secret_expression_in_key"}


def test_redact_secret_expressions_recurses_through_values() -> None:
    value = {
        "keep ${{ VARS.api.key }}": [
            "keep ${{ VARS.api.value }}",
            {"nested": "${{ SECRETS.api.TOKEN }}"},
        ]
    }

    assert redact_secret_expressions(value) == {
        "keep ${{ VARS.api.key }}": [
            "keep ${{ VARS.api.value }}",
            {"nested": MASK_VALUE},
        ]
    }


@pytest.mark.parametrize(
    "expression",
    [
        '${{ inputs.content || "fallback" }}',
        "${{ FN.to_base64(inputs.content) }}",
    ],
)
def test_compound_template_input_dependency_is_redacted(expression: str) -> None:
    state = _state(
        source_args={"content": "${{ SECRETS.api.TOKEN }}"},
        runtime_inputs={"content": "runtime-secret"},
    )

    prepared = state.prepare_step_args(
        "core.cases.create_comment", {"content": expression}
    )

    assert prepared == {"content": MASK_VALUE}


def test_template_input_dependencies_preserve_structured_paths() -> None:
    state = _state(
        source_args={
            "context": {
                "safe": "plain value",
                "token": "${{ SECRETS.api.TOKEN }}",
            }
        },
        runtime_inputs={
            "context": {
                "safe": "plain value",
                "token": "runtime-secret",
            }
        },
    )

    prepared = state.prepare_step_args(
        "core.cases.create_case",
        {
            "payload": {
                "safe": "${{ inputs.context.safe }}",
                "token": "${{ inputs.context.token }}",
            }
        },
    )

    assert prepared == {
        "payload": {
            "safe": "plain value",
            "token": MASK_VALUE,
        }
    }


def test_whole_structured_input_is_tree_masked_without_re_evaluation() -> None:
    state = _state(
        source_args={
            "context": {
                "safe": "${{ ACTIONS.fetch.result.note }}",
                "token": "${{ SECRETS.api.TOKEN }}",
            }
        },
        runtime_inputs={
            "context": {
                "safe": "${{ SECRETS.injected.VALUE }}",
                "token": "runtime-secret",
            }
        },
    )

    prepared = state.prepare_step_args(
        "core.table.insert_row",
        {"table": "events", "row_data": "${{ inputs.context }}"},
    )

    assert prepared == {
        "table": "events",
        "row_data": {
            "safe": "${{ SECRETS.injected.VALUE }}",
            "token": MASK_VALUE,
        },
    }


def test_reordered_runtime_mapping_masks_by_key() -> None:
    # expects-model validation may reorder mapping keys; pairing must not
    # rely on insertion order.
    state = _state(
        source_args={
            "context": {
                "safe": "plain",
                "token": "${{ SECRETS.api.TOKEN }}",
            }
        },
        runtime_inputs={
            "context": {
                "token": "runtime-secret",
                "safe": "plain",
            }
        },
    )

    prepared = state.prepare_step_args(
        "core.table.insert_row",
        {"row_data": "${{ inputs.context }}"},
    )

    assert prepared == {
        "row_data": {
            "token": MASK_VALUE,
            "safe": "plain",
        }
    }


def test_dynamic_authored_key_falls_back_to_conservative_mask() -> None:
    state = _state(
        source_args={
            "context": {
                "${{ VARS.col }}": "plain",
                "token": "${{ SECRETS.api.TOKEN }}",
            }
        },
        runtime_inputs={
            "context": {
                "events": "plain",
                "token": "runtime-secret",
            }
        },
        variables={"col": "events"},
    )

    prepared = state.prepare_step_args(
        "core.table.insert_row",
        {"row_data": "${{ inputs.context }}"},
    )

    assert prepared == {
        "row_data": {
            "events": MASK_VALUE,
            "token": MASK_VALUE,
        }
    }


def test_secret_dependent_input_key_is_rejected_only_at_sink() -> None:
    state = _state(
        source_args={
            "content": "plain",
            "context": {"${{ SECRETS.api.KEY }}": "value"},
        },
        runtime_inputs={
            "content": "plain",
            "context": {"runtime-key": "value"},
        },
    )

    assert state.prepare_step_args(
        "core.cases.create_comment",
        {"content": "${{ inputs.content }}"},
    ) == {"content": "plain"}

    with pytest.raises(TracecatExpressionError) as exc_info:
        state.prepare_step_args(
            "core.cases.create_case",
            {"payload": "${{ inputs.context }}"},
        )

    assert exc_info.value.detail == {"code": "secret_expression_in_key"}


def test_compound_dependency_propagates_across_template_boundaries() -> None:
    outer = _state(
        source_args={"value": "${{ SECRETS.api.TOKEN }}"},
        runtime_inputs={"value": "runtime-secret"},
    )

    child = outer.child_provenance({"value": '${{ inputs.value || "fallback" }}'})

    assert child["value"].dependency is True
    assert child["value"].source == '${{ inputs.value || "fallback" }}'


def test_substitution_splices_caller_source_without_evaluating() -> None:
    """Direct input references become the caller's raw source, verbatim."""
    substituted = substitute_source_references(
        {
            "content": "${{ inputs.content }}",
            "note": "Token: ${{ inputs.content }}",
            "step": "${{ steps.prev.result }}",
        },
        {"inputs": {"content": "${{ ACTIONS.fetch.result.body }}"}},
    )

    assert substituted == {
        "content": "${{ ACTIONS.fetch.result.body }}",
        "note": "Token: ${{ ACTIONS.fetch.result.body }}",
        "step": "${{ steps.prev.result }}",
    }


def test_substitution_keeps_unresolvable_references() -> None:
    substituted = substitute_source_references(
        {"content": "${{ inputs.missing }}"},
        {"inputs": {"other": "value"}},
    )

    assert substituted == {"content": "${{ inputs.missing }}"}


def test_caller_scoped_source_resolves_to_runtime_value_at_sink() -> None:
    """Regression: upstream action data through a sink must not become None."""
    state = _state(
        source_args={"content": "${{ ACTIONS.fetch.result.body }}"},
        runtime_inputs={"content": "evaluated-upstream-value"},
    )

    prepared = state.prepare_step_args(
        "core.cases.create_comment",
        {"case_id": "case-123", "content": "${{ inputs.content }}"},
    )

    assert prepared == {
        "case_id": "case-123",
        "content": "evaluated-upstream-value",
    }


def test_whole_mixed_string_input_is_conservatively_masked() -> None:
    state = _state(
        source_args={
            "content": "${{ ACTIONS.fetch.result.body }} ${{ SECRETS.api.TOKEN }}"
        },
        runtime_inputs={"content": "upstream s3cret"},
    )

    prepared = state.prepare_step_args(
        "core.cases.create_comment", {"content": "${{ inputs.content }}"}
    )

    assert prepared == {"content": MASK_VALUE}


def test_adjacent_templates_mask_independently() -> None:
    state = _state(
        source_args={"a": "left", "b": "${{ SECRETS.api.TOKEN }}"},
        runtime_inputs={"a": "left", "b": "runtime-secret"},
    )
    assert (
        derive_source_provenance({"a": "left", "b": "${{ SECRETS.api.TOKEN }}"})[
            "b"
        ].dependency
        is True
    )

    prepared = state.prepare_step_args(
        "core.cases.create_comment",
        {"content": "${{ inputs.a }} ${{ inputs.b }}"},
    )

    assert prepared == {"content": f"left {MASK_VALUE}"}


def test_step_results_are_runtime_data_without_taint() -> None:
    state = _state(
        source_args={"token": "${{ SECRETS.api.TOKEN }}"},
        runtime_inputs={"token": "runtime-secret"},
    )
    state.record_step("normalize", {"result": "runtime-secret"})
    state.record_step("plain", {"result": "ok"})

    prepared = state.prepare_step_args(
        "core.cases.create_comment",
        {"content": "${{ steps.normalize.result }} / ${{ steps.plain.result }}"},
    )

    assert prepared == {"content": "runtime-secret / ok"}


def test_step_results_do_not_taint_child_provenance() -> None:
    state = _state(
        source_args={"token": "${{ SECRETS.api.TOKEN }}"},
        runtime_inputs={"token": "runtime-secret"},
    )
    state.record_step("normalize", {"result": "runtime-secret"})

    child = state.child_provenance({"value": "${{ steps.normalize.result }}"})

    assert child["value"].dependency is False


def test_workflow_metadata_redacts_secret_expressions() -> None:
    prepared = prepare_action_args(
        "core.workflow.create_workflow",
        {
            "title": "Sync ${{ SECRETS.api.KEY }}",
            "description": "Uses ${{ VARS.api.host }}",
        },
        {"VARS": {"api": {"host": "api.example.com"}}},
    )

    assert prepared == {
        "title": f"Sync {MASK_VALUE}",
        "description": "Uses api.example.com",
    }


def test_preset_metadata_redacts_secret_expressions() -> None:
    prepared = prepare_action_args(
        "ai.agent.update_preset",
        {
            "slug": "analyst",
            "description": "Uses ${{ SECRETS.api.KEY }}",
            "name": "Analyst ${{ VARS.api.env }}",
        },
        {"VARS": {"api": {"env": "prod"}}},
    )

    assert prepared == {
        "slug": "analyst",
        "description": f"Uses {MASK_VALUE}",
        "name": "Analyst prod",
    }


def test_column_definitions_redact_secret_expressions() -> None:
    prepared = prepare_action_args(
        "core.table.create_table",
        {
            "name": "alerts",
            "columns": [
                {
                    "name": "token",
                    "type": "TEXT",
                    "default": "${{ SECRETS.api.KEY }}",
                },
                {
                    "name": "region",
                    "type": "TEXT",
                    "default": "${{ VARS.api.region }}",
                },
            ],
        },
        {"VARS": {"api": {"region": "us-east-1"}}},
    )

    assert prepared == {
        "name": "alerts",
        "columns": [
            {"name": "token", "type": "TEXT", "default": MASK_VALUE},
            {"name": "region", "type": "TEXT", "default": "us-east-1"},
        ],
    }


def test_preserve_substitutes_caller_source_for_direct_reference() -> None:
    ops = [
        {
            "op": "add",
            "path": "/definition/actions/-",
            "value": {"token": "${{ SECRETS.source.TOKEN }}"},
        }
    ]
    state = _state(
        source_args={"ops": ops},
        runtime_inputs={"ops": [{"op": "add", "value": {"token": "runtime-secret"}}]},
    )

    prepared = state.prepare_step_args(
        "core.workflow.edit_workflow",
        {"workflow_id": "wf-123", "patch_ops": "${{ inputs.ops }}"},
    )

    assert prepared == {"workflow_id": "wf-123", "patch_ops": ops}


def test_preserve_carrier_source_materializes_runtime_value() -> None:
    """A caller source that is itself a carrier resolves to the runtime value."""
    runtime_ops = [{"op": "add", "path": "/x", "value": 1}]
    state = _state(
        source_args={"ops": "${{ ACTIONS.builder.result }}"},
        runtime_inputs={"ops": runtime_ops},
    )

    prepared = state.prepare_step_args(
        "core.workflow.edit_workflow",
        {"workflow_id": "wf-123", "patch_ops": "${{ inputs.ops }}"},
    )

    assert prepared == {"workflow_id": "wf-123", "patch_ops": runtime_ops}


def test_secret_dependent_carrier_stays_preserved_in_template() -> None:
    state = _state(
        source_args={"ops": "${{ SECRETS.api.OPS }}"},
        runtime_inputs={"ops": "runtime-secret"},
    )

    prepared = state.prepare_step_args(
        "core.workflow.edit_workflow",
        {"workflow_id": "wf-123", "patch_ops": "${{ inputs.ops }}"},
    )

    assert prepared == {
        "workflow_id": "wf-123",
        "patch_ops": "${{ SECRETS.api.OPS }}",
    }


def test_preserve_untainted_step_carrier_materializes() -> None:
    runtime_ops = [{"op": "add", "path": "/x", "value": 1}]
    state = _state(
        source_args={},
        runtime_inputs={},
    )
    state.record_step("build", {"result": runtime_ops})

    prepared = state.prepare_step_args(
        "core.workflow.edit_workflow",
        {"workflow_id": "wf-123", "patch_ops": "${{ steps.build.result }}"},
    )

    assert prepared == {"workflow_id": "wf-123", "patch_ops": runtime_ops}


def test_preserve_step_carrier_materializes_without_step_taint() -> None:
    state = _state(
        source_args={"token": "${{ SECRETS.api.TOKEN }}"},
        runtime_inputs={"token": "runtime-secret"},
    )
    state.record_step("build", {"result": "runtime-secret"})

    prepared = state.prepare_step_args(
        "core.workflow.edit_workflow",
        {"workflow_id": "wf-123", "patch_ops": "${{ steps.build.result }}"},
    )

    assert prepared == {
        "workflow_id": "wf-123",
        "patch_ops": "runtime-secret",
    }


def test_preserve_carrier_expression_materializes_at_root() -> None:
    """A bare expression can never be valid preserved source; evaluate it."""
    ops = [{"op": "add", "path": "/definition/actions/-", "value": "${{ VARS.x }}"}]
    prepared = prepare_action_args(
        "core.workflow.edit_workflow",
        {
            "workflow_id": "wf-123",
            "patch_ops": "${{ ACTIONS.builder.result }}",
            "validate_only": False,
        },
        {"ACTIONS": {"builder": {"result": ops}}},
    )

    assert prepared == {
        "workflow_id": "wf-123",
        "patch_ops": ops,
        "validate_only": False,
    }


def test_preserve_carrier_expression_materializes_definition_yaml() -> None:
    yaml_source = "definition:\n  title: Generated\n"
    prepared = prepare_action_args(
        "core.workflow.create_workflow",
        {"definition_yaml": "${{ ACTIONS.gen.result }}"},
        {"ACTIONS": {"gen": {"result": yaml_source}}},
    )

    assert prepared == {"definition_yaml": yaml_source}


def test_secret_dependent_carrier_stays_preserved_at_root() -> None:
    prepared = prepare_action_args(
        "core.workflow.edit_workflow",
        {"workflow_id": "wf-123", "patch_ops": "${{ SECRETS.api.OPS }}"},
        {"SECRETS": {"api": {"OPS": "runtime-secret"}}},
    )

    assert prepared == {
        "workflow_id": "wf-123",
        "patch_ops": "${{ SECRETS.api.OPS }}",
    }


def test_preserve_literal_source_is_untouched_by_carrier_carveout() -> None:
    source = {"op": "add", "path": "/x", "value": "${{ ACTIONS.a.result }}"}
    prepared = prepare_action_args(
        "core.workflow.edit_workflow",
        {"workflow_id": "wf-123", "patch_ops": [source]},
        {},
    )

    assert prepared == {"workflow_id": "wf-123", "patch_ops": [source]}


def test_source_provenance_never_evaluates_authored_source() -> None:
    provenance = derive_source_provenance(
        {"content": "${{ FN.uuid4() }} ${{ SECRETS.api.TOKEN }}"},
    )

    assert provenance["content"].dependency is True
    assert provenance["content"].source == (
        "${{ FN.uuid4() }} ${{ SECRETS.api.TOKEN }}"
    )


def test_partition_redacts_before_evaluation_and_restores_order() -> None:
    args = {
        "case_id": "${{ VARS.case.id }}",
        "content": "Host ${{ VARS.api.host }}, token ${{ SECRETS.api.TOKEN }}",
        "workflow_id": None,
    }

    partitioned = partition_action_args("core.cases.create_comment", args)

    assert partitioned.resolvable == {
        "case_id": "${{ VARS.case.id }}",
        "content": f"Host ${{{{ VARS.api.host }}}}, token {MASK_VALUE}",
        "workflow_id": None,
    }
    assert partitioned.merge(
        {
            "case_id": "case-123",
            "content": f"Host example.com, token {MASK_VALUE}",
            "workflow_id": None,
        }
    ) == {
        "case_id": "case-123",
        "content": f"Host example.com, token {MASK_VALUE}",
        "workflow_id": None,
    }


def test_partition_restores_preserved_subtree_without_reordering() -> None:
    source = {
        "${{ VARS.source.key }}": ["${{ SECRETS.source.TOKEN }}"],
    }
    args = {
        "workflow_id": "${{ VARS.runtime.workflow_id }}",
        "patch_ops": source,
        "validate_only": False,
    }

    partitioned = partition_action_args("core.workflow.edit_workflow", args)

    assert partitioned.resolvable == {
        "workflow_id": "${{ VARS.runtime.workflow_id }}",
        "validate_only": False,
    }
    assert partitioned.merge({"workflow_id": "wf-123", "validate_only": False}) == {
        "workflow_id": "wf-123",
        "patch_ops": source,
        "validate_only": False,
    }


def test_derive_secret_dependencies_does_not_taint_step_results() -> None:
    dependencies = derive_secret_dependencies(
        {"value": "${{ steps.normalize.result }}"}
    )

    assert dependencies == {"value": False}


def test_preserve_splices_null_input_value_but_leaves_missing_reference() -> None:
    state = _state(
        source_args={"optional": None, "present": "abc"},
        runtime_inputs={"optional": None, "present": "abc"},
    )
    patch_ops = [
        {"op": "replace", "path": "/config/timeout", "value": "${{ inputs.optional }}"},
        {"op": "replace", "path": "/config/name", "value": "${{ inputs.present }}"},
        {"op": "replace", "path": "/config/ghost", "value": "${{ inputs.missing }}"},
    ]

    prepared = state.prepare_step_args(
        "core.workflow.edit_workflow", {"patch_ops": patch_ops}
    )

    assert prepared["patch_ops"] == [
        {"op": "replace", "path": "/config/timeout", "value": None},
        {"op": "replace", "path": "/config/name", "value": "abc"},
        {"op": "replace", "path": "/config/ghost", "value": "${{ inputs.missing }}"},
    ]


def test_create_case_tags_redact_secret_expressions_at_root() -> None:
    partitioned = partition_action_args(
        "core.cases.create_case",
        {
            "summary": "Case",
            "tags": ["${{ SECRETS.api.KEY }}", "phishing"],
            "create_missing_tags": True,
        },
    )

    assert partitioned.resolvable["tags"] == [MASK_VALUE, "phishing"]
    assert partitioned.resolvable["create_missing_tags"] is True
