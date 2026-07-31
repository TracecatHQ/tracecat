from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, NamedTuple, cast

from tracecat.exceptions import TracecatExpressionError
from tracecat.expressions import patterns
from tracecat.expressions.common import ExprContext
from tracecat.expressions.core import (
    Expression,
    SecretPathExtractor,
    TemplateExpression,
)
from tracecat.expressions.eval import eval_templated_object
from tracecat.expressions.parser.core import parser
from tracecat.secrets.constants import MASK_VALUE


class ExpressionPolicy(StrEnum):
    """Controls expression evaluation for one top-level action parameter."""

    RESOLVE = "resolve"
    PRESERVE = "preserve"
    REDACT_SECRETS = "redact_secrets"


class ActionParameter(NamedTuple):
    """Identifies one parameter on a registry action."""

    action: str
    parameter: str


_PRESERVE = ExpressionPolicy.PRESERVE
_REDACT_SECRETS = ExpressionPolicy.REDACT_SECRETS

# PRESERVE: workflow-authoring source executed later; evaluating it here is wrong.
# REDACT_SECRETS: free-form content persisted into Tracecat-owned durable state.
# Unlisted parameters default to RESOLVE.
POLICY_MAP: Mapping[ActionParameter, ExpressionPolicy] = {
    ActionParameter("core.workflow.edit_workflow", "patch_ops"): _PRESERVE,
    ActionParameter("core.workflow.create_workflow", "definition_yaml"): _PRESERVE,
    ActionParameter("core.cases.create_case", "summary"): _REDACT_SECRETS,
    ActionParameter("core.cases.create_case", "description"): _REDACT_SECRETS,
    ActionParameter("core.cases.create_case", "fields"): _REDACT_SECRETS,
    ActionParameter("core.cases.create_case", "payload"): _REDACT_SECRETS,
    ActionParameter("core.cases.update_case", "summary"): _REDACT_SECRETS,
    ActionParameter("core.cases.update_case", "description"): _REDACT_SECRETS,
    ActionParameter("core.cases.update_case", "fields"): _REDACT_SECRETS,
    ActionParameter("core.cases.update_case", "payload"): _REDACT_SECRETS,
    ActionParameter("core.cases.create_comment", "content"): _REDACT_SECRETS,
    ActionParameter("core.cases.reply_to_comment", "content"): _REDACT_SECRETS,
    ActionParameter("core.cases.update_comment", "content"): _REDACT_SECRETS,
    ActionParameter("core.table.insert_row", "row_data"): _REDACT_SECRETS,
    ActionParameter("core.table.insert_rows", "rows_data"): _REDACT_SECRETS,
    ActionParameter("core.table.update_row", "row_data"): _REDACT_SECRETS,
    ActionParameter("core.cases.insert_row", "row"): _REDACT_SECRETS,
    ActionParameter("ai.agent.create_preset", "instructions"): _REDACT_SECRETS,
    ActionParameter("ai.agent.update_preset", "instructions"): _REDACT_SECRETS,
}


def _expression_references_secrets(expression: str) -> bool:
    extractor = SecretPathExtractor()
    results = Expression(expression, visitor=extractor).visit()
    return bool(results.get(ExprContext.SECRETS))


def _redact_secret_string(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        expression = match.group("expr")
        if expression and _expression_references_secrets(expression):
            return MASK_VALUE
        return match.group("template")

    return patterns.TEMPLATE_STRING.sub(replace, value)


def redact_secret_expressions(value: Any) -> Any:
    """Replace complete secret-dependent expression occurrences recursively."""
    match value:
        case str():
            return _redact_secret_string(value)
        case list():
            return [redact_secret_expressions(item) for item in value]
        case dict():
            redacted: dict[Any, Any] = {}
            for key, item in value.items():
                if isinstance(key, str):
                    redacted_key = _redact_secret_string(key)
                    if redacted_key != key:
                        raise TracecatExpressionError(
                            "Secret expressions are not allowed in dictionary keys",
                            detail={"code": "secret_expression_in_key"},
                        )
                else:
                    redacted_key = key
                redacted[redacted_key] = redact_secret_expressions(item)
            return redacted
        case _:
            return value


def _is_direct_template_reference(template: str) -> bool:
    """Return whether a template is exactly an inputs.* reference.

    Never expand steps.*: step results are materialized runtime data, and
    splicing them in as evaluable source would let fetched content execute
    expressions.
    """
    match = patterns.TEMPLATE_STRING.fullmatch(template)
    if match is None or not (expression := match.group("expr")):
        return False
    parse_tree = parser.parse(expression)
    return parse_tree is not None and parse_tree.data == "template_action_inputs"


def expand_template_source_references(value: Any, context: Mapping[str, Any]) -> Any:
    """Expand direct template input references while retaining nested source."""
    match value:
        case str() if _is_direct_template_reference(value):
            return TemplateExpression(value, operand=context).result()
        case str():

            def replace(match: re.Match[str]) -> str:
                template = match.group("template")
                if _is_direct_template_reference(template):
                    return str(TemplateExpression(template, operand=context).result())
                return template

            return patterns.TEMPLATE_STRING.sub(replace, value)
        case list():
            return [expand_template_source_references(item, context) for item in value]
        case dict():
            expanded: dict[Any, Any] = {}
            for key, item in value.items():
                expanded_key = (
                    expand_template_source_references(key, context)
                    if isinstance(key, str)
                    else key
                )
                if expanded_key in expanded:
                    raise TracecatExpressionError(
                        "Template source expansion produced a duplicate dictionary key",
                        detail={"code": "template_source_key_collision"},
                    )
                expanded[expanded_key] = expand_template_source_references(
                    item, context
                )
            return expanded
        case _:
            return value


@dataclass(frozen=True, slots=True)
class PartitionedActionArgs:
    """Action arguments split into resolvable and preserved parameters."""

    action: str
    original: Mapping[str, Any]
    resolvable: Mapping[str, Any]

    def merge(self, evaluated: Mapping[str, Any]) -> dict[str, Any]:
        """Restore preserved values without changing parameter order."""
        return {
            parameter: (
                value
                if expression_policy(self.action, parameter)
                is ExpressionPolicy.PRESERVE
                else evaluated[parameter]
            )
            for parameter, value in self.original.items()
        }


def expression_policy(action: str, parameter: str) -> ExpressionPolicy:
    """Return the expression policy for an action parameter."""
    return POLICY_MAP.get(ActionParameter(action, parameter), ExpressionPolicy.RESOLVE)


def partition_action_args(
    action: str, args: Mapping[str, Any]
) -> PartitionedActionArgs:
    """Apply pre-evaluation policy and exclude preserved source subtrees."""
    resolvable: dict[str, Any] = {}
    for parameter, value in args.items():
        match expression_policy(action, parameter):
            case ExpressionPolicy.PRESERVE:
                continue
            case ExpressionPolicy.REDACT_SECRETS:
                resolvable[parameter] = redact_secret_expressions(value)
            case ExpressionPolicy.RESOLVE:
                resolvable[parameter] = value
    return PartitionedActionArgs(
        action=action,
        original=args,
        resolvable=resolvable,
    )


def prepare_action_args(
    action: str,
    args: Mapping[str, Any],
    context: Mapping[str, Any],
) -> dict[str, Any]:
    """Apply field policy and evaluate one action's arguments."""
    partitioned = partition_action_args(action, args)
    evaluated = cast(
        Mapping[str, Any],
        eval_templated_object(partitioned.resolvable, operand=context),
    )
    return partitioned.merge(evaluated)
