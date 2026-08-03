import pytest

from tracecat.exceptions import TracecatExpressionError
from tracecat.executor.expression_policy import (
    POLICY_MAP,
    ActionParameter,
    ExpressionPolicy,
    derive_secret_dependencies,
    expand_template_source_references,
    expression_policy,
    partition_action_args,
    prepare_action_args,
    redact_secret_expressions,
)
from tracecat.secrets.constants import MASK_VALUE


def test_action_parameter_policy_scope_is_explicit() -> None:
    preserved = {
        parameter
        for parameter, policy in POLICY_MAP.items()
        if policy is ExpressionPolicy.PRESERVE
    }
    redacted = {
        parameter
        for parameter, policy in POLICY_MAP.items()
        if policy is ExpressionPolicy.REDACT_SECRETS
    }

    assert preserved == {
        ActionParameter(
            action="core.workflow.edit_workflow",
            parameter="patch_ops",
        ),
        ActionParameter(
            action="core.workflow.create_workflow",
            parameter="definition_yaml",
        ),
    }
    assert redacted == {
        ActionParameter(action="core.cases.create_case", parameter="summary"),
        ActionParameter(action="core.cases.create_case", parameter="description"),
        ActionParameter(action="core.cases.create_case", parameter="fields"),
        ActionParameter(action="core.cases.create_case", parameter="payload"),
        ActionParameter(action="core.cases.update_case", parameter="summary"),
        ActionParameter(action="core.cases.update_case", parameter="description"),
        ActionParameter(action="core.cases.update_case", parameter="fields"),
        ActionParameter(action="core.cases.update_case", parameter="payload"),
        ActionParameter(action="core.cases.create_comment", parameter="content"),
        ActionParameter(action="core.cases.reply_to_comment", parameter="content"),
        ActionParameter(action="core.cases.update_comment", parameter="content"),
        ActionParameter(action="core.table.insert_row", parameter="row_data"),
        ActionParameter(action="core.table.insert_rows", parameter="rows_data"),
        ActionParameter(action="core.table.update_row", parameter="row_data"),
        ActionParameter(action="core.cases.insert_row", parameter="row"),
        ActionParameter(action="ai.agent.create_preset", parameter="instructions"),
        ActionParameter(action="ai.agent.update_preset", parameter="instructions"),
    }


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
    dependencies = derive_secret_dependencies({"content": "${{ SECRETS.api.TOKEN }}"})
    assert isinstance(dependencies, dict)

    prepared = prepare_action_args(
        "core.cases.create_comment",
        {"content": expression},
        {"inputs": {"content": "runtime-secret"}},
        dependencies,
    )

    assert prepared == {"content": MASK_VALUE}


def test_template_input_dependencies_preserve_structured_paths() -> None:
    dependencies = derive_secret_dependencies(
        {
            "context": {
                "safe": "plain source",
                "token": "${{ SECRETS.api.TOKEN }}",
            }
        }
    )
    assert isinstance(dependencies, dict)

    prepared = prepare_action_args(
        "core.cases.create_case",
        {
            "payload": {
                "safe": "${{ inputs.context.safe }}",
                "token": "${{ inputs.context.token }}",
            }
        },
        {
            "inputs": {
                "context": {
                    "safe": "plain value",
                    "token": "runtime-secret",
                }
            }
        },
        dependencies,
    )

    assert prepared == {
        "payload": {
            "safe": "plain value",
            "token": MASK_VALUE,
        }
    }


def test_compound_dependency_propagates_across_template_boundaries() -> None:
    outer_dependencies = derive_secret_dependencies(
        {"value": "${{ SECRETS.api.TOKEN }}"}
    )
    assert isinstance(outer_dependencies, dict)
    inner_dependencies = derive_secret_dependencies(
        {"value": '${{ inputs.value || "fallback" }}'},
        outer_dependencies,
    )

    assert inner_dependencies == {"value": True}


@pytest.mark.parametrize(
    "source",
    [
        "${{ ACTIONS.fetch.result.body }}",
        "${{ steps.prev.result }}",
        "${{ TRIGGER.payload }}",
        '${{ inputs.other || "x" }}',
        "${{ var.item }}",
    ],
)
def test_expand_keeps_reference_for_caller_scoped_source(source: str) -> None:
    """Non-portable caller source must not be spliced into template scope."""
    expanded = expand_template_source_references(
        {
            "content": "${{ inputs.content }}",
            "note": "Token: ${{ inputs.content }}",
        },
        {"inputs": {"content": source}},
    )

    assert expanded == {
        "content": "${{ inputs.content }}",
        "note": "Token: ${{ inputs.content }}",
    }


def test_caller_scoped_source_resolves_to_runtime_value_at_sink() -> None:
    """Regression: upstream action data through a sink must not become None."""
    source_context = {"inputs": {"content": "${{ ACTIONS.fetch.result.body }}"}}
    step_args = {"case_id": "case-123", "content": "${{ inputs.content }}"}

    expanded = expand_template_source_references(step_args, source_context)
    dependencies = derive_secret_dependencies(source_context["inputs"])
    assert isinstance(dependencies, dict)

    prepared = prepare_action_args(
        "core.cases.create_comment",
        expanded,
        {"inputs": {"content": "evaluated-upstream-value"}},
        dependencies,
    )

    assert prepared == {
        "case_id": "case-123",
        "content": "evaluated-upstream-value",
    }


def test_portable_secret_source_still_expands_for_granular_redaction() -> None:
    source = "Host ${{ VARS.api.host }}, token ${{ SECRETS.api.TOKEN }}"

    expanded = expand_template_source_references(
        {"content": "${{ inputs.content }}"},
        {"inputs": {"content": source}},
    )

    assert expanded == {"content": source}


def test_non_portable_secret_source_masks_whole_expression() -> None:
    """Secret mixed with caller-scoped context falls back to the dependency tree."""
    source_context = {
        "inputs": {
            "content": "${{ ACTIONS.fetch.result.body }} ${{ SECRETS.api.TOKEN }}"
        }
    }
    step_args = {"content": "${{ inputs.content }}"}

    expanded = expand_template_source_references(step_args, source_context)
    assert expanded == step_args

    dependencies = derive_secret_dependencies(source_context["inputs"])
    assert isinstance(dependencies, dict)

    prepared = prepare_action_args(
        "core.cases.create_comment",
        expanded,
        {"inputs": {"content": "runtime-value"}},
        dependencies,
    )

    assert prepared == {"content": MASK_VALUE}


def test_adjacent_templates_do_not_crash_analysis() -> None:
    """Regression: lazy fullmatch must not span "${{ a }} ${{ b }}"."""
    source_context = {"inputs": {"a": "left", "b": "${{ SECRETS.api.TOKEN }}"}}
    args = {"content": "${{ inputs.a }} ${{ inputs.b }}"}

    dependencies = derive_secret_dependencies(source_context["inputs"])
    assert dependencies == {"a": False, "b": True}

    expanded = expand_template_source_references(args, source_context)
    assert expanded == {"content": "left ${{ SECRETS.api.TOKEN }}"}


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
