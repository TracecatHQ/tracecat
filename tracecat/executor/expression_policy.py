from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, NamedTuple, cast

from lark import Token

from tracecat.exceptions import TracecatExpressionError
from tracecat.expressions import patterns
from tracecat.expressions.common import ExprContext, eval_jsonpath
from tracecat.expressions.core import TemplateExpression
from tracecat.expressions.eval import eval_templated_object
from tracecat.expressions.parser.core import parser
from tracecat.secrets.constants import MASK_VALUE

type SecretDependency = bool | list[SecretDependency] | dict[Any, SecretDependency]
type SecretDependencies = Mapping[str, SecretDependency]

_SECRET_KEY_DEPENDENCY = object()


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


def _has_secret_dependency(dependency: SecretDependency) -> bool:
    match dependency:
        case bool():
            return dependency
        case list():
            return any(_has_secret_dependency(item) for item in dependency)
        case dict():
            return any(_has_secret_dependency(item) for item in dependency.values())


def _input_dependency(
    path: str,
    input_dependencies: SecretDependencies,
) -> SecretDependency | None:
    dependency = eval_jsonpath(
        f"{ExprContext.TEMPLATE_ACTION_INPUTS}{path}",
        {ExprContext.TEMPLATE_ACTION_INPUTS: input_dependencies},
    )
    if dependency is not None:
        return cast(SecretDependency, dependency)

    # A compound expression can collapse a structured input's dependency to
    # True. Any later access beneath that input must remain conservatively
    # secret-dependent even though the boolean tree has no child to traverse.
    match = re.match(r"^\.([A-Za-z_][A-Za-z0-9_]*)", path)
    if match is None:
        return True if _has_secret_dependency(dict(input_dependencies)) else None
    return input_dependencies.get(match.group(1))


def _expression_depends_on_secrets(
    expression: str,
    input_dependencies: SecretDependencies | None = None,
) -> bool:
    parse_tree = parser.parse(expression)
    if parse_tree is None:
        raise TracecatExpressionError(
            f"Parser returned None for expression {expression!r}"
        )
    if next(parse_tree.find_data("secrets"), None) is not None:
        return True
    if not input_dependencies:
        return False

    for node in parse_tree.find_data("template_action_inputs"):
        token = node.children[0]
        if not isinstance(token, Token):
            raise TracecatExpressionError(
                f"Expected template input path token, got {type(token).__name__}"
            )
        dependency = _input_dependency(str(token), input_dependencies)
        if dependency is not None and _has_secret_dependency(dependency):
            return True
    return False


def _redact_secret_string(
    value: str,
    input_dependencies: SecretDependencies | None = None,
) -> str:
    def replace(match: re.Match[str]) -> str:
        expression = match.group("expr")
        if expression and _expression_depends_on_secrets(
            expression, input_dependencies
        ):
            return MASK_VALUE
        return match.group("template")

    return patterns.TEMPLATE_STRING.sub(replace, value)


def redact_secret_expressions(
    value: Any,
    input_dependencies: SecretDependencies | None = None,
) -> Any:
    """Replace complete secret-dependent expression occurrences recursively."""
    match value:
        case str():
            return _redact_secret_string(value, input_dependencies)
        case list():
            return [
                redact_secret_expressions(item, input_dependencies) for item in value
            ]
        case dict():
            redacted: dict[Any, Any] = {}
            for key, item in value.items():
                if isinstance(key, str):
                    redacted_key = _redact_secret_string(key, input_dependencies)
                    if redacted_key != key:
                        raise TracecatExpressionError(
                            "Secret expressions are not allowed in dictionary keys",
                            detail={"code": "secret_expression_in_key"},
                        )
                else:
                    redacted_key = key
                redacted[redacted_key] = redact_secret_expressions(
                    item, input_dependencies
                )
            return redacted
        case _:
            return value


def derive_secret_dependencies(
    value: Any,
    input_dependencies: SecretDependencies | None = None,
) -> SecretDependency:
    """Derive expression-level secret dependencies without evaluating values."""
    match value:
        case str():
            # Lazy fullmatch spans adjacent templates ("${{ a }} ${{ b }}"),
            # so gate on the standalone pattern before extracting.
            direct_match = (
                patterns.TEMPLATE_STRING.fullmatch(value)
                if patterns.STANDALONE_TEMPLATE.match(value)
                else None
            )
            if direct_match is not None and (expression := direct_match.group("expr")):
                parse_tree = parser.parse(expression)
                if (
                    parse_tree is not None
                    and parse_tree.data == "template_action_inputs"
                    and input_dependencies
                ):
                    token = parse_tree.children[0]
                    if not isinstance(token, Token):
                        raise TracecatExpressionError(
                            "Expected template input path token"
                        )
                    dependency = _input_dependency(str(token), input_dependencies)
                    return dependency if dependency is not None else False

            return any(
                _expression_depends_on_secrets(expression, input_dependencies)
                for match in patterns.TEMPLATE_STRING.finditer(value)
                if (expression := match.group("expr")) is not None
            )
        case list():
            return [
                derive_secret_dependencies(item, input_dependencies) for item in value
            ]
        case dict():
            dependencies = {
                key: derive_secret_dependencies(item, input_dependencies)
                for key, item in value.items()
            }
            if any(
                isinstance(key, str)
                and _has_secret_dependency(
                    derive_secret_dependencies(key, input_dependencies)
                )
                for key in value
            ):
                dependencies[_SECRET_KEY_DEPENDENCY] = True
            return dependencies
        case _:
            return False


# Context references a template's evaluation scope cannot resolve. Caller
# source containing them must stay behind its inputs.* reference; the
# dependency tree still governs redaction for that reference.
_NON_PORTABLE_NODES = frozenset(
    {
        "actions",
        "trigger",
        "local_vars",
        "local_vars_assignment",
        "template_action_inputs",
        "template_action_steps",
    }
)


def _is_portable_source(value: Any) -> bool:
    """Whether expanded caller source can evaluate inside a template scope."""
    match value:
        case str():
            for match_ in patterns.TEMPLATE_STRING.finditer(value):
                expression = match_.group("expr")
                if not expression:
                    continue
                try:
                    parse_tree = parser.parse(expression)
                except TracecatExpressionError:
                    return False
                if parse_tree is None:
                    return False
                if any(
                    subtree.data in _NON_PORTABLE_NODES
                    for subtree in parse_tree.iter_subtrees()
                ):
                    return False
            return True
        case list():
            return all(_is_portable_source(item) for item in value)
        case dict():
            return all(
                _is_portable_source(key) and _is_portable_source(item)
                for key, item in value.items()
            )
        case _:
            return True


def _is_direct_template_reference(template: str) -> bool:
    """Return whether a template is exactly an inputs.* reference.

    Never expand steps.*: step results are materialized runtime data, and
    splicing them in as evaluable source would let fetched content execute
    expressions.
    """
    if patterns.STANDALONE_TEMPLATE.match(template) is None:
        return False
    match = patterns.TEMPLATE_STRING.fullmatch(template)
    if match is None or not (expression := match.group("expr")):
        return False
    parse_tree = parser.parse(expression)
    return parse_tree is not None and parse_tree.data == "template_action_inputs"


def expand_template_source_references(value: Any, context: Mapping[str, Any]) -> Any:
    """Expand direct template input references while retaining nested source."""
    match value:
        case str() if _is_direct_template_reference(value):
            expanded = TemplateExpression(value, operand=context).result()
            return expanded if _is_portable_source(expanded) else value
        case str():

            def replace(match: re.Match[str]) -> str:
                template = match.group("template")
                if _is_direct_template_reference(template):
                    expanded = TemplateExpression(template, operand=context).result()
                    if _is_portable_source(expanded):
                        return str(expanded)
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
    action: str,
    args: Mapping[str, Any],
    input_dependencies: SecretDependencies | None = None,
) -> PartitionedActionArgs:
    """Apply pre-evaluation policy and exclude preserved source subtrees."""
    resolvable: dict[str, Any] = {}
    for parameter, value in args.items():
        match expression_policy(action, parameter):
            case ExpressionPolicy.PRESERVE:
                continue
            case ExpressionPolicy.REDACT_SECRETS:
                resolvable[parameter] = redact_secret_expressions(
                    value, input_dependencies
                )
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
    input_dependencies: SecretDependencies | None = None,
) -> dict[str, Any]:
    """Apply field policy and evaluate one action's arguments."""
    partitioned = partition_action_args(action, args, input_dependencies)
    evaluated = cast(
        Mapping[str, Any],
        eval_templated_object(partitioned.resolvable, operand=context),
    )
    return partitioned.merge(evaluated)
