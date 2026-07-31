import pytest

from tracecat.exceptions import TracecatExpressionError
from tracecat.executor.expression_policy import (
    POLICY_MAP,
    ActionParameter,
    ExpressionPolicy,
    expression_policy,
    partition_action_args,
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
