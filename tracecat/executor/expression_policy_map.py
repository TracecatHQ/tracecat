"""Per-parameter expression policy assignments for registry actions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum


class ExpressionPolicy(StrEnum):
    """Controls expression evaluation for one top-level action parameter."""

    RESOLVE = "resolve"
    PRESERVE = "preserve"
    REDACT_SECRETS = "redact_secrets"


@dataclass(frozen=True, slots=True)
class ActionParameter:
    """Identifies one parameter on a registry action."""

    action: str
    parameter: str


# PRESERVE: workflow-authoring source executed later; evaluating it here is wrong.
# REDACT_SECRETS: free-form content persisted into Tracecat-owned durable state.
# Unlisted parameters default to RESOLVE.
POLICY_MAP: Mapping[ActionParameter, ExpressionPolicy] = {
    ActionParameter(
        "core.workflow.edit_workflow", "patch_ops"
    ): ExpressionPolicy.PRESERVE,
    ActionParameter(
        "core.workflow.create_workflow", "definition_yaml"
    ): ExpressionPolicy.PRESERVE,
    ActionParameter(
        "core.workflow.create_workflow", "title"
    ): ExpressionPolicy.REDACT_SECRETS,
    ActionParameter(
        "core.workflow.create_workflow", "description"
    ): ExpressionPolicy.REDACT_SECRETS,
    ActionParameter(
        "core.cases.create_case", "summary"
    ): ExpressionPolicy.REDACT_SECRETS,
    ActionParameter(
        "core.cases.create_case", "description"
    ): ExpressionPolicy.REDACT_SECRETS,
    ActionParameter(
        "core.cases.create_case", "fields"
    ): ExpressionPolicy.REDACT_SECRETS,
    ActionParameter(
        "core.cases.create_case", "payload"
    ): ExpressionPolicy.REDACT_SECRETS,
    ActionParameter("core.cases.create_case", "tags"): ExpressionPolicy.REDACT_SECRETS,
    ActionParameter(
        "core.cases.create_case", "dropdown_values"
    ): ExpressionPolicy.REDACT_SECRETS,
    ActionParameter(
        "core.cases.update_case", "summary"
    ): ExpressionPolicy.REDACT_SECRETS,
    ActionParameter(
        "core.cases.update_case", "description"
    ): ExpressionPolicy.REDACT_SECRETS,
    ActionParameter(
        "core.cases.update_case", "fields"
    ): ExpressionPolicy.REDACT_SECRETS,
    ActionParameter(
        "core.cases.update_case", "payload"
    ): ExpressionPolicy.REDACT_SECRETS,
    ActionParameter("core.cases.update_case", "tags"): ExpressionPolicy.REDACT_SECRETS,
    ActionParameter(
        "core.cases.update_case", "dropdown_values"
    ): ExpressionPolicy.REDACT_SECRETS,
    ActionParameter(
        "core.cases.create_comment", "content"
    ): ExpressionPolicy.REDACT_SECRETS,
    ActionParameter(
        "core.cases.reply_to_comment", "content"
    ): ExpressionPolicy.REDACT_SECRETS,
    ActionParameter(
        "core.cases.update_comment", "content"
    ): ExpressionPolicy.REDACT_SECRETS,
    ActionParameter("core.table.create_table", "name"): ExpressionPolicy.REDACT_SECRETS,
    ActionParameter(
        "core.table.create_table", "columns"
    ): ExpressionPolicy.REDACT_SECRETS,
    ActionParameter(
        "core.table.create_column", "column"
    ): ExpressionPolicy.REDACT_SECRETS,
    ActionParameter(
        "core.table.update_column", "update"
    ): ExpressionPolicy.REDACT_SECRETS,
    ActionParameter(
        "core.table.insert_row", "row_data"
    ): ExpressionPolicy.REDACT_SECRETS,
    ActionParameter(
        "core.table.insert_rows", "rows_data"
    ): ExpressionPolicy.REDACT_SECRETS,
    ActionParameter(
        "core.table.update_row", "row_data"
    ): ExpressionPolicy.REDACT_SECRETS,
    ActionParameter("core.cases.insert_row", "row"): ExpressionPolicy.REDACT_SECRETS,
    ActionParameter(
        "ai.agent.create_preset", "instructions"
    ): ExpressionPolicy.REDACT_SECRETS,
    ActionParameter(
        "ai.agent.update_preset", "instructions"
    ): ExpressionPolicy.REDACT_SECRETS,
    ActionParameter("ai.agent.create_preset", "name"): ExpressionPolicy.REDACT_SECRETS,
    ActionParameter(
        "ai.agent.create_preset", "description"
    ): ExpressionPolicy.REDACT_SECRETS,
    ActionParameter("ai.agent.update_preset", "name"): ExpressionPolicy.REDACT_SECRETS,
    ActionParameter(
        "ai.agent.update_preset", "description"
    ): ExpressionPolicy.REDACT_SECRETS,
    ActionParameter(
        "ai.agent.update_preset", "new_slug"
    ): ExpressionPolicy.REDACT_SECRETS,
    ActionParameter("ai.agent.create_preset", "slug"): ExpressionPolicy.REDACT_SECRETS,
    ActionParameter(
        "ai.agent.create_preset", "base_url"
    ): ExpressionPolicy.REDACT_SECRETS,
    ActionParameter(
        "ai.agent.update_preset", "base_url"
    ): ExpressionPolicy.REDACT_SECRETS,
    ActionParameter(
        "ai.agent.create_preset", "output_type"
    ): ExpressionPolicy.REDACT_SECRETS,
    ActionParameter(
        "ai.agent.update_preset", "output_type"
    ): ExpressionPolicy.REDACT_SECRETS,
}


def expression_policy(action: str, parameter: str) -> ExpressionPolicy:
    """Return the expression policy for an action parameter."""
    return POLICY_MAP.get(ActionParameter(action, parameter), ExpressionPolicy.RESOLVE)
