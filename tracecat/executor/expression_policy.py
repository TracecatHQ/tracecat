from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, NamedTuple, cast

from lark import Token, Tree

from tracecat.dsl.schemas import TemplateExecutionContext
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
    ActionParameter("core.workflow.create_workflow", "title"): _REDACT_SECRETS,
    ActionParameter("core.workflow.create_workflow", "description"): _REDACT_SECRETS,
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


def has_secret_dependency(dependency: SecretDependency) -> bool:
    """Collapse a dependency tree to whether any part depends on secrets."""
    match dependency:
        case bool():
            return dependency
        case list():
            return any(has_secret_dependency(item) for item in dependency)
        case dict():
            return any(has_secret_dependency(item) for item in dependency.values())


def _scoped_dependency(
    path: str,
    dependencies: SecretDependencies,
    context: ExprContext,
) -> SecretDependency | None:
    dependency = eval_jsonpath(
        f"{context}{path}",
        {context: dependencies},
    )
    if dependency is not None:
        return cast(SecretDependency, dependency)

    # A compound expression can collapse a structured entry's dependency to
    # True. Any later access beneath that entry must remain conservatively
    # secret-dependent even though the boolean tree has no child to traverse.
    match = re.match(r"^\.([A-Za-z_][A-Za-z0-9_]*)", path)
    if match is None:
        return True if has_secret_dependency(dict(dependencies)) else None
    return dependencies.get(match.group(1))


def _scope_reference_depends_on_secrets(
    parse_tree: Tree[Token],
    node_name: str,
    dependencies: SecretDependencies,
    context: ExprContext,
) -> bool:
    for node in parse_tree.find_data(node_name):
        token = node.children[0]
        if not isinstance(token, Token):
            raise TracecatExpressionError(
                f"Expected {node_name} path token, got {type(token).__name__}"
            )
        dependency = _scoped_dependency(str(token), dependencies, context)
        if dependency is not None and has_secret_dependency(dependency):
            return True
    return False


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
    if input_dependencies and _scope_reference_depends_on_secrets(
        parse_tree,
        "template_action_inputs",
        input_dependencies,
        ExprContext.TEMPLATE_ACTION_INPUTS,
    ):
        return True
    return False


def _redact_secret_string(
    value: str,
    input_dependencies: SecretDependencies | None = None,
) -> str:
    def replace(match: re.Match[str]) -> str:
        expression = match.group("expr")
        if not expression or not _expression_depends_on_secrets(
            expression, input_dependencies
        ):
            return match.group("template")
        return MASK_VALUE

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
                    # Keys never take the projection path: a masked or
                    # projected key is ambiguous, so secret-dependent keys
                    # are rejected outright.
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
                    dependency = _scoped_dependency(
                        str(token),
                        input_dependencies,
                        ExprContext.TEMPLATE_ACTION_INPUTS,
                    )
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
                and has_secret_dependency(
                    derive_secret_dependencies(key, input_dependencies)
                )
                for key in value
            ):
                dependencies[_SECRET_KEY_DEPENDENCY] = True
            return dependencies
        case _:
            return False


def _direct_template_input_path(template: str) -> str | None:
    """Return the path for an exact inputs.* reference."""
    if patterns.STANDALONE_TEMPLATE.match(template) is None:
        return None
    match = patterns.TEMPLATE_STRING.fullmatch(template)
    if match is None or not (expression := match.group("expr")):
        return None
    parse_tree = parser.parse(expression)
    if parse_tree is None or parse_tree.data != "template_action_inputs":
        return None
    token = parse_tree.children[0]
    if not isinstance(token, Token):
        raise TracecatExpressionError("Expected template input path token")
    return str(token)


def _is_direct_template_reference(template: str) -> bool:
    """Return whether a template is exactly an inputs.* reference.

    Never substitute steps.*: step results are materialized runtime data,
    not authored source.
    """
    return _direct_template_input_path(template) is not None


def _redact_runtime_value(value: Any, dependency: SecretDependency) -> Any:
    """Mask materialized input data according to its authored dependency tree.

    Runtime containers retain their shape. Mapping dependencies are paired by
    insertion order because non-secret expressions may change authored keys
    during the caller's normal evaluation.
    """
    match dependency:
        case bool():
            if not dependency:
                return value
            if isinstance(value, list):
                return [_redact_runtime_value(item, True) for item in value]
            if isinstance(value, Mapping):
                return {
                    key: _redact_runtime_value(item, True)
                    for key, item in value.items()
                }
            return MASK_VALUE
        case list():
            if not isinstance(value, list) or len(value) != len(dependency):
                return _redact_runtime_value(value, has_secret_dependency(dependency))
            return [
                _redact_runtime_value(item, item_dependency)
                for item, item_dependency in zip(value, dependency, strict=True)
            ]
        case dict():
            key_dependency = dependency.get(_SECRET_KEY_DEPENDENCY, False)
            if has_secret_dependency(key_dependency):
                raise TracecatExpressionError(
                    "Secret expressions are not allowed in dictionary keys",
                    detail={"code": "secret_expression_in_key"},
                )
            value_dependencies = [
                item_dependency
                for key, item_dependency in dependency.items()
                if key is not _SECRET_KEY_DEPENDENCY
            ]
            if not isinstance(value, Mapping) or len(value) != len(value_dependencies):
                return _redact_runtime_value(value, has_secret_dependency(dependency))
            return {
                key: _redact_runtime_value(item, item_dependency)
                for (key, item), item_dependency in zip(
                    value.items(), value_dependencies, strict=True
                )
            }


def substitute_source_references(value: Any, source_operand: Mapping[str, Any]) -> Any:
    """Replace direct template input references with the caller's raw source.

    Substitution splices authored source as inert data; it never evaluates
    it, so the result is safe regardless of which contexts the source
    references. Unresolvable references are left as written.
    """
    match value:
        case str() if _is_direct_template_reference(value):
            substituted = TemplateExpression(value, operand=source_operand).result()
            return value if substituted is None else substituted
        case str():

            def replace(match: re.Match[str]) -> str:
                template = match.group("template")
                if _is_direct_template_reference(template):
                    substituted = TemplateExpression(
                        template, operand=source_operand
                    ).result()
                    if substituted is not None:
                        return str(substituted)
                return template

            return patterns.TEMPLATE_STRING.sub(replace, value)
        case list():
            return [
                substitute_source_references(item, source_operand) for item in value
            ]
        case dict():
            substituted: dict[Any, Any] = {}
            for key, item in value.items():
                substituted_key = (
                    substitute_source_references(key, source_operand)
                    if isinstance(key, str)
                    else key
                )
                if substituted_key in substituted:
                    raise TracecatExpressionError(
                        "Source substitution produced a duplicate dictionary key",
                        detail={"code": "template_source_key_collision"},
                    )
                substituted[substituted_key] = substitute_source_references(
                    item, source_operand
                )
            return substituted
        case _:
            return value


def _is_resolvable_carrier(
    value: Any,
    input_dependencies: SecretDependencies | None,
) -> bool:
    """Whether a preserved value is a dynamic carrier safe to evaluate.

    A bare standalone expression can never itself be valid preserved source
    (patch ops must be a list, a definition must be YAML text), so the caller
    is constructing the source dynamically. Evaluate it unless it depends on
    secrets; secret-dependent carriers stay preserved and fail loudly at the
    action's own validation instead of leaking.
    """
    if not isinstance(value, str):
        return False
    if patterns.STANDALONE_TEMPLATE.match(value) is None:
        return False
    match = patterns.TEMPLATE_STRING.fullmatch(value)
    if match is None or not (expression := match.group("expr")):
        return False
    try:
        return not _expression_depends_on_secrets(expression, input_dependencies)
    except TracecatExpressionError:
        return False


@dataclass(frozen=True, slots=True)
class SourceProvenance:
    """Authored source and secret dependency for one template argument."""

    source: Any
    dependency: SecretDependency


def derive_source_provenance(
    args: Mapping[str, Any],
) -> dict[str, SourceProvenance]:
    """Build unevaluated caller-boundary provenance for a template invocation."""
    return {
        parameter: SourceProvenance(
            source=value,
            dependency=derive_secret_dependencies(value),
        )
        for parameter, value in args.items()
    }


class TemplateExecutionState:
    """Pairs a template's materialized context with argument provenance."""

    def __init__(
        self,
        context: TemplateExecutionContext,
        provenance: Mapping[str, SourceProvenance],
    ) -> None:
        self._context = context
        self._provenance = dict(provenance)

    @property
    def context(self) -> TemplateExecutionContext:
        return self._context

    def _input_dependencies(self) -> dict[str, SecretDependency]:
        return {
            parameter: provenance.dependency
            for parameter, provenance in self._provenance.items()
        }

    def _source_operand(self) -> dict[str, Any]:
        # Defaulted parameters have no provenance; their validated values are
        # the template author's literals and stand in as source.
        sources: dict[str, Any] = dict(self._context.get("inputs") or {})
        for parameter, provenance in self._provenance.items():
            sources[parameter] = provenance.source
        return {str(ExprContext.TEMPLATE_ACTION_INPUTS): sources}

    def record_step(self, ref: str, result: Any) -> None:
        """Store materialized step data without interpreting it as source."""
        self._context["steps"][ref] = result

    def prepare_step_args(self, action: str, args: Mapping[str, Any]) -> dict[str, Any]:
        """Apply each parameter's policy and evaluate one step's arguments."""
        prepared: dict[str, Any] = {}
        for parameter, value in args.items():
            match expression_policy(action, parameter):
                case ExpressionPolicy.RESOLVE:
                    prepared[parameter] = eval_templated_object(
                        value, operand=cast(Mapping[str, Any], self._context)
                    )
                case ExpressionPolicy.REDACT_SECRETS:
                    prepared[parameter] = self.project_redacted(value)
                case ExpressionPolicy.PRESERVE:
                    prepared[parameter] = self._preserve(value)
        return prepared

    def project_redacted(self, value: Any) -> Any:
        """Evaluate a protected field while masking authored input dependencies.

        Exact tainted input references are returned directly from materialized
        runtime data after tree-shaped masking. They are never recursively
        evaluated as expression source. Compound tainted expressions are
        replaced before ordinary evaluation.
        """
        input_dependencies = self._input_dependencies()
        match value:
            case str():
                if (path := _direct_template_input_path(value)) is not None:
                    dependency = _scoped_dependency(
                        path,
                        input_dependencies,
                        ExprContext.TEMPLATE_ACTION_INPUTS,
                    )
                    if dependency is not None and has_secret_dependency(dependency):
                        runtime_value = eval_jsonpath(
                            f"{ExprContext.TEMPLATE_ACTION_INPUTS}{path}",
                            cast(Mapping[str, Any], self._context),
                        )
                        return _redact_runtime_value(runtime_value, dependency)
                redacted = redact_secret_expressions(value, input_dependencies)
                return eval_templated_object(
                    redacted, operand=cast(Mapping[str, Any], self._context)
                )
            case list():
                return [self.project_redacted(item) for item in value]
            case dict():
                projected: dict[Any, Any] = {}
                for key, item in value.items():
                    if isinstance(key, str):
                        redacted_key = redact_secret_expressions(
                            key, input_dependencies
                        )
                        if redacted_key != key:
                            raise TracecatExpressionError(
                                "Secret expressions are not allowed in dictionary keys",
                                detail={"code": "secret_expression_in_key"},
                            )
                        projected_key = eval_templated_object(
                            key, operand=cast(Mapping[str, Any], self._context)
                        )
                    else:
                        projected_key = key
                    if projected_key in projected:
                        raise TracecatExpressionError(
                            "Redaction produced a duplicate dictionary key",
                            detail={"code": "redacted_key_collision"},
                        )
                    projected[projected_key] = self.project_redacted(item)
                return projected
            case _:
                return value

    def _preserve(self, value: Any) -> Any:
        substituted = substitute_source_references(value, self._source_operand())
        if _is_resolvable_carrier(substituted, self._input_dependencies()):
            # A bare expression is never valid preserved source. When the
            # caller's own source is the carrier, its runtime value is
            # already materialized in inputs — evaluate the original
            # reference in this scope instead of the caller's text.
            target = (
                value
                if isinstance(value, str) and substituted != value
                else substituted
            )
            return eval_templated_object(
                target, operand=cast(Mapping[str, Any], self._context)
            )
        return substituted

    def child_provenance(self, args: Mapping[str, Any]) -> dict[str, SourceProvenance]:
        """Build provenance for a nested template invocation's arguments."""
        provenance: dict[str, SourceProvenance] = {}
        for parameter, value in args.items():
            provenance[parameter] = SourceProvenance(
                source=substitute_source_references(value, self._source_operand()),
                dependency=derive_secret_dependencies(
                    value, self._input_dependencies()
                ),
            )
        return provenance


@dataclass(frozen=True, slots=True)
class PartitionedActionArgs:
    """Action arguments split into resolvable and preserved parameters."""

    action: str
    original: Mapping[str, Any]
    resolvable: Mapping[str, Any]

    def merge(self, evaluated: Mapping[str, Any]) -> dict[str, Any]:
        """Restore preserved values without changing parameter order."""
        return {
            parameter: (evaluated[parameter] if parameter in self.resolvable else value)
            for parameter, value in self.original.items()
        }


def expression_policy(action: str, parameter: str) -> ExpressionPolicy:
    """Return the expression policy for an action parameter."""
    return POLICY_MAP.get(ActionParameter(action, parameter), ExpressionPolicy.RESOLVE)


def partition_action_args(
    action: str,
    args: Mapping[str, Any],
) -> PartitionedActionArgs:
    """Apply pre-evaluation policy and exclude preserved source subtrees.

    Root-level boundary: template steps route through
    ``TemplateExecutionState.prepare_step_args`` instead, which carries
    input provenance.
    """
    resolvable: dict[str, Any] = {}
    for parameter, value in args.items():
        match expression_policy(action, parameter):
            case ExpressionPolicy.PRESERVE:
                if _is_resolvable_carrier(value, None):
                    resolvable[parameter] = value
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
    """Apply field policy and evaluate one action's arguments at the root."""
    partitioned = partition_action_args(action, args)
    evaluated = cast(
        Mapping[str, Any],
        eval_templated_object(partitioned.resolvable, operand=context),
    )
    return partitioned.merge(evaluated)
