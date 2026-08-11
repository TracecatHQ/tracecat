"""Field policy and secret provenance for expression resolution.

Provenance is built once per template invocation as a plain mapping from input
names to authored source and secret-dependent relative paths. The mapping is
then threaded into the existing expression traversal through resolution-policy
hooks that run immediately before each expression is evaluated.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from lark import Token, Tree

from tracecat.exceptions import TracecatExpressionError
from tracecat.expressions import patterns
from tracecat.expressions.common import eval_jsonpath
from tracecat.expressions.eval import eval_templated_object
from tracecat.expressions.parser.core import parser
from tracecat.secrets.constants import MASK_VALUE

__all__ = (
    "ActionArgumentPlan",
    "ExpressionPolicy",
    "ProvenanceMap",
    "build_provenance",
    "expression_policy",
    "resolve_action_args",
)

type PathSegment = str | int
type DataPath = tuple[PathSegment, ...]

_PATH_SEGMENT = re.compile(
    r"""
    (?:
        \.(?P<field>[A-Za-z_][A-Za-z0-9_]*)
        |\[(?P<index>-?\d+)\]
        |\[(?P<quoted>"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')\]
    )
    """,
    re.VERBOSE,
)
_ROOT_INPUT = re.compile(r"^\.([A-Za-z_][A-Za-z0-9_]*)")


class ExpressionPolicy(StrEnum):
    """Controls expression resolution for one action parameter."""

    RESOLVE = "resolve"
    """Evaluate expressions normally in the current runtime context."""

    PRESERVE = "preserve"
    """Retain caller-authored source.

    Template-local ``inputs.*`` references are replaced with the source that
    was originally passed in.
    """

    REDACT_SECRETS = "redact_secrets"
    """Resolve non-secret expressions and mask secret-dependent expressions."""


PRESERVE_PARAMETERS = frozenset(
    {
        ("core.workflow.edit_workflow", "patch_ops"),
        ("core.workflow.create_workflow", "definition_yaml"),
    }
)

REDACT_PARAMETERS: Mapping[str, frozenset[str]] = {
    "core.workflow.create_workflow": frozenset({"title", "description"}),
    "core.cases.create_case": frozenset(
        {"summary", "description", "fields", "payload", "tags", "dropdown_values"}
    ),
    "core.cases.update_case": frozenset(
        {"summary", "description", "fields", "payload", "tags", "dropdown_values"}
    ),
    "core.cases.create_comment": frozenset({"content"}),
    "core.cases.reply_to_comment": frozenset({"content"}),
    "core.cases.update_comment": frozenset({"content"}),
    "core.table.create_table": frozenset({"name", "columns"}),
    "core.table.create_column": frozenset({"column"}),
    "core.table.update_column": frozenset({"update"}),
    "core.table.insert_row": frozenset({"row_data"}),
    "core.table.insert_rows": frozenset({"rows_data"}),
    "core.table.update_row": frozenset({"row_data"}),
    "core.cases.insert_row": frozenset({"row"}),
    "ai.agent.create_preset": frozenset(
        {"instructions", "name", "description", "slug", "base_url", "output_type"}
    ),
    "ai.agent.update_preset": frozenset(
        {
            "instructions",
            "name",
            "description",
            "new_slug",
            "base_url",
            "output_type",
        }
    ),
}


def expression_policy(action: str, parameter: str) -> ExpressionPolicy:
    """Return the policy for an exact action-parameter pair."""
    if (action, parameter) in PRESERVE_PARAMETERS:
        return ExpressionPolicy.PRESERVE
    if parameter in REDACT_PARAMETERS.get(action, frozenset()):
        return ExpressionPolicy.REDACT_SECRETS
    return ExpressionPolicy.RESOLVE


@dataclass(frozen=True, slots=True)
class _InputProvenance:
    """Authored source and secret-dependent paths for one template input."""

    source: Any
    """Caller-authored value before expression evaluation."""

    secret_paths: frozenset[DataPath]
    """Relative value paths that directly or transitively depend on secrets."""

    secret_key_paths: frozenset[DataPath]
    """Relative mapping paths containing secret-dependent keys."""


type ProvenanceMap = Mapping[str, _InputProvenance]


@dataclass(frozen=True, slots=True)
class _Dependencies:
    values: frozenset[DataPath] = frozenset()
    """Secret-dependent value paths relative to the scanned value."""

    keys: frozenset[DataPath] = frozenset()
    """Mapping paths containing secret-dependent keys."""

    @property
    def secret(self) -> bool:
        return bool(self.values or self.keys)

    def prefixed(self, segment: PathSegment) -> _Dependencies:
        return _Dependencies(
            values=frozenset((segment, *path) for path in self.values),
            keys=frozenset((segment, *path) for path in self.keys),
        )


@dataclass(frozen=True, slots=True)
class _InputSelection:
    source_found: bool
    """Whether the selected path exists in the authored source."""

    source: Any
    """Authored value selected by the input expression."""

    dependencies: _Dependencies
    """Secret dependencies scoped to the selected input path."""


def build_provenance(
    arguments: Mapping[str, Any],
    parent: ProvenanceMap | None = None,
) -> dict[str, _InputProvenance]:
    """Scan authored arguments once and build the template-input mapping."""
    provenance: dict[str, _InputProvenance] = {}
    for parameter, value in arguments.items():
        dependencies = _derive_dependencies(value, parent)
        source = (
            eval_templated_object(
                value,
                policy=_PreservePolicy(parent),
            )
            if parent is not None
            else value
        )
        provenance[parameter] = _InputProvenance(
            source=source,
            secret_paths=dependencies.values,
            secret_key_paths=dependencies.keys,
        )
    return provenance


@dataclass(frozen=True, slots=True)
class _RedactionPolicy:
    """Mask secret-dependent ASTs before ordinary expression evaluation."""

    provenance: ProvenanceMap | None = None
    """Input provenance used to detect transitive secret dependencies."""

    reject: bool = False
    """Whether a secret dependency raises instead of returning a mask."""

    defer: bool = False
    """Whether safe expressions remain authored source instead of evaluating."""

    def resolve(
        self,
        source: str,
        tree: Tree[Token],
        default: Callable[[], Any],
        *,
        standalone: bool,
    ) -> Any:
        """Mask secret dependencies or delegate to ordinary evaluation."""
        del standalone
        if (
            self.provenance is not None
            and (path := _direct_input_path_from_tree(tree)) is not None
        ):
            selection = _select_input(path, self.provenance)
            if selection is None or not selection.dependencies.secret:
                return source if self.defer else default()
            if self.reject or selection.dependencies.keys:
                _raise_secret_key_error()
            return MASK_VALUE if self.defer else _mask_runtime_value(default())

        if _tree_dependencies(tree, self.provenance).secret:
            if self.reject:
                _raise_secret_key_error()
            return MASK_VALUE

        return source if self.defer else default()


@dataclass(frozen=True, slots=True)
class _PreservePolicy:
    """Resolve template inputs to authored source without evaluating that source."""

    provenance: ProvenanceMap
    """Input provenance used to recover caller-authored source."""

    materialize_carrier: bool = False
    """Whether a safe standalone field expression may resolve to runtime data."""

    def resolve(
        self,
        source: str,
        tree: Tree[Token],
        default: Callable[[], Any],
        *,
        standalone: bool,
    ) -> Any:
        """Substitute input source and materialize only a safe field carrier."""
        if (path := _direct_input_path_from_tree(tree)) is not None:
            selection = _select_input(path, self.provenance)
            if selection is None or not selection.source_found:
                return source
            authored = selection.source
            if (
                self.materialize_carrier
                and standalone
                and not selection.dependencies.secret
                and isinstance(authored, str)
                and _standalone_expression(authored) is not None
            ):
                return default()
            return authored

        if (
            self.materialize_carrier
            and standalone
            and not _tree_dependencies(tree, self.provenance).secret
        ):
            return default()
        return source


@dataclass(frozen=True, slots=True)
class ActionArgumentPlan:
    """Root arguments after policy is applied but before secret collection."""

    action: str
    """Fully qualified action name used for parameter-policy lookup."""

    original: Mapping[str, Any]
    """Original caller-authored action arguments."""

    evaluable: Mapping[str, Any]
    """Policy-filtered arguments safe for expression dependency collection."""

    @classmethod
    def build(
        cls,
        action: str,
        arguments: Mapping[str, Any],
    ) -> ActionArgumentPlan:
        redaction_policy = _RedactionPolicy(defer=True)
        key_policy = _RedactionPolicy(reject=True, defer=True)
        evaluable: dict[str, Any] = {}
        for parameter, value in arguments.items():
            match expression_policy(action, parameter):
                case ExpressionPolicy.RESOLVE:
                    evaluable[parameter] = value
                case ExpressionPolicy.REDACT_SECRETS:
                    evaluable[parameter] = eval_templated_object(
                        value,
                        policy=redaction_policy,
                        key_policy=key_policy,
                    )
                case ExpressionPolicy.PRESERVE:
                    if _is_safe_carrier(value, None):
                        evaluable[parameter] = value
        return cls(action=action, original=arguments, evaluable=evaluable)

    def evaluate(self, context: Mapping[str, Any]) -> dict[str, Any]:
        """Evaluate the planned subset and restore preserved parameters."""
        return resolve_action_args(
            self.action,
            self.original,
            context,
            {},
        )


def resolve_action_args(
    action: str,
    arguments: Mapping[str, Any],
    context: Mapping[str, Any],
    provenance: ProvenanceMap,
) -> dict[str, Any]:
    """Resolve one template step at the target action's policy boundary."""
    redaction_policy = _RedactionPolicy(provenance)
    key_policy = _RedactionPolicy(provenance, reject=True)
    resolved: dict[str, Any] = {}

    for parameter, value in arguments.items():
        match expression_policy(action, parameter):
            case ExpressionPolicy.RESOLVE:
                resolved[parameter] = eval_templated_object(value, operand=context)
            case ExpressionPolicy.REDACT_SECRETS:
                resolved[parameter] = eval_templated_object(
                    value,
                    operand=context,
                    policy=redaction_policy,
                    key_policy=key_policy,
                )
            case ExpressionPolicy.PRESERVE:
                preserve_policy = _PreservePolicy(
                    provenance,
                    materialize_carrier=_is_safe_carrier(value, provenance),
                )
                resolved[parameter] = eval_templated_object(
                    value,
                    operand=context,
                    policy=preserve_policy,
                )
    return resolved


def _derive_dependencies(
    value: Any,
    parent: ProvenanceMap | None,
) -> _Dependencies:
    match value:
        case str():
            if (
                parent is not None
                and (path := _direct_input_path(value)) is not None
                and (selection := _select_input(path, parent)) is not None
            ):
                return selection.dependencies

            secret = False
            secret_keys = False
            for match in patterns.TEMPLATE_STRING.finditer(value):
                expression = match.group("expr")
                if not expression:
                    continue
                tree_dependencies = _tree_dependencies(
                    _parse_expression(expression),
                    parent,
                )
                secret = secret or tree_dependencies.secret
                secret_keys = secret_keys or bool(tree_dependencies.keys)
            return _Dependencies(
                values=frozenset({()}) if secret else frozenset(),
                keys=frozenset({()}) if secret_keys else frozenset(),
            )
        case list():
            return _merge_dependencies(
                _derive_dependencies(item, parent).prefixed(index)
                for index, item in enumerate(value)
            )
        case dict():
            dependencies: list[_Dependencies] = []
            dynamic_key = False
            secret_key = False
            for key, item in value.items():
                if isinstance(key, str):
                    key_dependencies = _derive_dependencies(key, parent)
                    secret_key = secret_key or key_dependencies.secret
                    dynamic_key = dynamic_key or bool(
                        patterns.TEMPLATE_STRING.search(key)
                    )
                dependencies.append(_derive_dependencies(item, parent).prefixed(key))
            merged = _merge_dependencies(dependencies)
            if secret_key:
                merged = _Dependencies(merged.values, merged.keys | {()})
            if dynamic_key and merged.values:
                merged = _Dependencies(frozenset({()}), merged.keys)
            return merged
        case _:
            return _Dependencies()


def _merge_dependencies(items: Iterable[_Dependencies]) -> _Dependencies:
    values: set[DataPath] = set()
    keys: set[DataPath] = set()
    for item in items:
        values.update(item.values)
        keys.update(item.keys)
    return _Dependencies(frozenset(values), frozenset(keys))


def _select_input(path: str, provenance: ProvenanceMap) -> _InputSelection | None:
    root_match = _ROOT_INPUT.match(path)
    if root_match is None:
        return None
    parameter = root_match.group(1)
    binding = provenance.get(parameter)
    if binding is None:
        return None

    segments = _parse_input_path(path)
    if segments is None or not isinstance(segments[0], str):
        dependencies = _Dependencies(
            values=frozenset({()}) if binding.secret_paths else frozenset(),
            keys=frozenset({()}) if binding.secret_key_paths else frozenset(),
        )
    else:
        relative_path = segments[1:]
        dependencies = _Dependencies(
            values=_select_paths(binding.secret_paths, relative_path),
            keys=_select_paths(binding.secret_key_paths, relative_path),
        )

    suffix = path[root_match.end() :]
    if not suffix:
        return _InputSelection(True, binding.source, dependencies)
    try:
        source = eval_jsonpath(
            f"source{suffix}",
            {"source": binding.source},
            strict=True,
        )
    except TracecatExpressionError:
        return _InputSelection(False, None, dependencies)
    return _InputSelection(True, source, dependencies)


def _select_paths(
    paths: frozenset[DataPath],
    prefix: DataPath,
) -> frozenset[DataPath]:
    selected: set[DataPath] = set()
    for path in paths:
        if path[: len(prefix)] == prefix:
            selected.add(path[len(prefix) :])
        elif prefix[: len(path)] == path:
            selected.add(())
    return frozenset(selected)


def _tree_dependencies(
    tree: Tree[Token],
    provenance: ProvenanceMap | None,
) -> _Dependencies:
    dependencies = [
        _Dependencies(values=frozenset({()})) for _ in tree.find_data("secrets")
    ]
    if provenance is None:
        return _merge_dependencies(dependencies)

    for node in tree.find_data("template_action_inputs"):
        token = node.children[0]
        if not isinstance(token, Token):
            raise TracecatExpressionError("Expected template input path token")
        if (selection := _select_input(str(token), provenance)) is not None:
            dependencies.append(selection.dependencies)
    return _merge_dependencies(dependencies)


def _mask_runtime_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _mask_runtime_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_mask_runtime_value(item) for item in value]
    return MASK_VALUE


def _is_safe_carrier(
    value: Any,
    provenance: ProvenanceMap | None,
) -> bool:
    if not isinstance(value, str):
        return False
    if (expression := _standalone_expression(value)) is None:
        return False
    try:
        tree = _parse_expression(expression)
    except TracecatExpressionError:
        return False
    return not _tree_dependencies(tree, provenance).secret


def _parse_expression(expression: str) -> Tree[Token]:
    tree = parser.parse(expression)
    if tree is None:
        raise TracecatExpressionError(
            f"Parser returned None for expression {expression!r}"
        )
    return tree


def _parse_input_path(path: str) -> DataPath | None:
    segments: list[PathSegment] = []
    position = 0
    while position < len(path):
        match = _PATH_SEGMENT.match(path, position)
        if match is None:
            return None
        if (field := match.group("field")) is not None:
            segments.append(field)
        elif (index := match.group("index")) is not None:
            segments.append(int(index))
        elif (quoted := match.group("quoted")) is not None:
            value = ast.literal_eval(quoted)
            if not isinstance(value, str):
                return None
            segments.append(value)
        position = match.end()
    return tuple(segments) if segments else None


def _standalone_expression(value: str) -> str | None:
    if patterns.STANDALONE_TEMPLATE.match(value) is None:
        return None
    match = patterns.TEMPLATE_STRING.fullmatch(value)
    if match is None or not (expression := match.group("expr")):
        return None
    return expression


def _direct_input_path(template: str) -> str | None:
    if (expression := _standalone_expression(template)) is None:
        return None
    return _direct_input_path_from_tree(_parse_expression(expression))


def _direct_input_path_from_tree(tree: Tree[Token]) -> str | None:
    if tree.data != "template_action_inputs":
        return None
    token = tree.children[0]
    if not isinstance(token, Token):
        raise TracecatExpressionError("Expected template input path token")
    return str(token)


def _raise_secret_key_error() -> None:
    raise TracecatExpressionError(
        "Secret expressions are not allowed in dictionary keys",
        detail={"code": "secret_expression_in_key"},
    )
