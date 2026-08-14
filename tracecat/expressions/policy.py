"""Field policy and secret provenance for expression resolution.

Provenance is built once per template invocation as a plain mapping from input
names to authored source and a secret-dependency trie mirroring the value's
shape.
The mapping is then threaded into the existing expression traversal through
resolution-policy hooks that run immediately before each expression is
evaluated.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
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
class _SecretDependencies:
    """Secret dependencies of one value, as a trie mirroring the value's shape.

    Empty subtrees are never stored: a child is present only if a secret
    dependency exists somewhere below it.
    """

    value: bool = False
    """Whether the value at this node depends on a secret."""

    keys: bool = False
    """Whether the mapping at this node contains secret-dependent keys."""

    children: Mapping[PathSegment, _SecretDependencies] = field(default_factory=dict)
    """Dependencies of nested container entries, keyed by field, index, or key."""

    @property
    def secret(self) -> bool:
        """Whether any value or key in this subtree depends on a secret."""
        return (
            self.value
            or self.keys
            or any(child.secret for child in self.children.values())
        )

    @property
    def secret_values(self) -> bool:
        """Whether any value in this subtree depends on a secret."""
        return self.value or any(
            child.secret_values for child in self.children.values()
        )

    @property
    def secret_keys(self) -> bool:
        """Whether any mapping in this subtree has secret-dependent keys."""
        return self.keys or any(child.secret_keys for child in self.children.values())

    def nested(self, segment: PathSegment) -> _SecretDependencies:
        """Wrap these dependencies one container level down, under ``segment``."""
        return (
            _SecretDependencies(children={segment: self})
            if self.secret
            else _NO_DEPENDENCIES
        )

    def select(self, path: DataPath) -> _SecretDependencies:
        """Dependencies of the subtree at ``path``, inheriting marked ancestors."""
        node = self
        value = False
        keys = False
        for segment in path:
            value = value or node.value
            keys = keys or node.keys
            node = node.children.get(segment, _NO_DEPENDENCIES)
        if not (value or keys):
            return node
        return _SecretDependencies(
            value=node.value or value,
            keys=node.keys or keys,
            children=node.children,
        )

    def collapsed(self) -> _SecretDependencies:
        """Collapse all dependencies onto the root, discarding path precision."""
        return _SecretDependencies(value=self.secret_values, keys=self.secret_keys)

    def without_values(self) -> _SecretDependencies:
        """Drop value dependencies everywhere, keeping key-dependency structure."""
        children = {
            segment: stripped
            for segment, child in self.children.items()
            if (stripped := child.without_values()).secret
        }
        return _SecretDependencies(keys=self.keys, children=children)

    @classmethod
    def merged(cls, items: Iterable[_SecretDependencies]) -> _SecretDependencies:
        """Union of several dependency tries, merged segment by segment."""
        value = False
        keys = False
        grouped: dict[PathSegment, list[_SecretDependencies]] = {}
        for item in items:
            value = value or item.value
            keys = keys or item.keys
            for segment, child in item.children.items():
                grouped.setdefault(segment, []).append(child)
        return cls(
            value=value,
            keys=keys,
            children={
                segment: cls.merged(children) for segment, children in grouped.items()
            },
        )


_NO_DEPENDENCIES = _SecretDependencies()


@dataclass(frozen=True, slots=True)
class _InputProvenance:
    """Authored source and secret dependencies for one template input."""

    source: Any
    """Caller-authored value before expression evaluation."""

    dependencies: _SecretDependencies
    """Secret dependencies that directly or transitively apply to the value."""


type ProvenanceMap = Mapping[str, _InputProvenance]


@dataclass(frozen=True, slots=True)
class _InputSelection:
    source_found: bool
    """Whether the selected path exists in the authored source."""

    source: Any
    """Authored value selected by the input expression."""

    dependencies: _SecretDependencies
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
            dependencies=dependencies,
        )
    return provenance


@dataclass(frozen=True, slots=True)
class _CollectionPolicy:
    """Mask secret expressions while leaving safe source unevaluated.

    Used before runtime context exists, so masking falls back to direct
    ``secrets.*`` detection without provenance.
    """

    reject: bool = False
    """Whether a secret dependency raises instead of returning a mask."""

    def resolve(
        self,
        source: str,
        tree: Tree[Token],
        default: Callable[[], Any],
        *,
        standalone: bool,
    ) -> Any:
        """Mask secret expressions and return everything else as source."""
        del standalone, default
        if _tree_dependencies(tree, None).secret:
            if self.reject:
                _raise_secret_key_error()
            return MASK_VALUE
        return source


@dataclass(frozen=True, slots=True)
class _RedactionPolicy:
    """Mask secret-dependent ASTs before ordinary expression evaluation."""

    provenance: ProvenanceMap
    """Input provenance used to detect transitive secret dependencies."""

    reject: bool = False
    """Whether a secret dependency raises instead of returning a mask."""

    def resolve(
        self,
        source: str,
        tree: Tree[Token],
        default: Callable[[], Any],
        *,
        standalone: bool,
    ) -> Any:
        """Mask secret dependencies or delegate to ordinary evaluation."""
        del source, standalone
        if (ref := _direct_input_ref(tree)) is not None:
            selection = _select_input(ref, self.provenance)
            if selection is None or not selection.dependencies.secret:
                return default()
            if self.reject or selection.dependencies.secret_keys:
                _raise_secret_key_error()
            return _mask_runtime_value(default())

        if _tree_dependencies(tree, self.provenance).secret:
            if self.reject:
                _raise_secret_key_error()
            return MASK_VALUE

        return default()


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
        if (ref := _direct_input_ref(tree)) is not None:
            selection = _select_input(ref, self.provenance)
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
        redaction_policy = _CollectionPolicy()
        key_policy = _CollectionPolicy(reject=True)
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
) -> _SecretDependencies:
    match value:
        case str():
            if (
                parent is not None
                and (ref := _template_input_ref(value)) is not None
                and (selection := _select_input(ref, parent)) is not None
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
                secret_keys = secret_keys or tree_dependencies.secret_keys
            return _SecretDependencies(value=secret, keys=secret_keys)
        case list():
            return _SecretDependencies.merged(
                _derive_dependencies(item, parent).nested(index)
                for index, item in enumerate(value)
            )
        case dict():
            collected: list[_SecretDependencies] = []
            dynamic_key = False
            secret_key = False
            for key, item in value.items():
                if isinstance(key, str):
                    secret_key = secret_key or _derive_dependencies(key, parent).secret
                    dynamic_key = dynamic_key or bool(
                        patterns.TEMPLATE_STRING.search(key)
                    )
                collected.append(_derive_dependencies(item, parent).nested(key))
            merged = _SecretDependencies.merged(collected)
            if secret_key:
                merged = replace(merged, keys=True)
            if dynamic_key and merged.secret_values:
                merged = replace(merged.without_values(), value=True)
            return merged
        case _:
            return _NO_DEPENDENCIES


def _select_input(
    ref: _InputRef,
    provenance: ProvenanceMap,
) -> _InputSelection | None:
    binding = provenance.get(ref.parameter)
    if binding is None:
        return None

    if ref.path is None:
        dependencies = binding.dependencies.collapsed()
    elif (
        dependency_path := _normalize_negative_indices(ref.path, binding.source)
    ) is None:
        dependencies = binding.dependencies.collapsed()
    else:
        dependencies = binding.dependencies.select(dependency_path)

    if not ref.suffix:
        return _InputSelection(True, binding.source, dependencies)
    try:
        source = eval_jsonpath(
            f"source{ref.suffix}",
            {"source": binding.source},
            strict=True,
        )
    except TracecatExpressionError:
        return _InputSelection(False, None, dependencies)
    return _InputSelection(True, source, dependencies)


def _normalize_negative_indices(path: DataPath, source: Any) -> DataPath | None:
    """Normalize negative list indices using the authored source shape.

    Return ``None`` when a negative index cannot be normalized so callers can
    conservatively collapse the dependency lookup instead of treating it as
    untainted.
    """
    if not any(isinstance(segment, int) and segment < 0 for segment in path):
        return path

    normalized: list[PathSegment] = []
    current = source
    for segment in path:
        if isinstance(current, list) and isinstance(segment, int):
            index = segment if segment >= 0 else len(current) + segment
            if not 0 <= index < len(current):
                return None
            normalized.append(index)
            current = current[index]
        elif isinstance(current, Mapping) and segment in current:
            normalized.append(segment)
            current = current[segment]
        else:
            return None
    return tuple(normalized)


def _tree_dependencies(
    tree: Tree[Token],
    provenance: ProvenanceMap | None,
) -> _SecretDependencies:
    collected = [_SecretDependencies(value=True) for _ in tree.find_data("secrets")]
    if provenance is not None:
        for node in tree.find_data("template_action_inputs"):
            if (ref := _direct_input_ref(node)) is None:
                continue
            if (selection := _select_input(ref, provenance)) is not None:
                collected.append(selection.dependencies)
    return _SecretDependencies.merged(collected)


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


@dataclass(frozen=True, slots=True)
class _InputRef:
    """One parsed ``inputs.<parameter><suffix>`` reference."""

    parameter: str
    """Referenced template input name."""

    suffix: str
    """Raw path suffix used to select within the authored source."""

    path: DataPath | None
    """Parsed suffix segments, or None when the suffix defies path parsing."""


def _parse_input_ref(path: str) -> _InputRef | None:
    root = _ROOT_INPUT.match(path)
    if root is None:
        return None
    suffix = path[root.end() :]
    return _InputRef(
        parameter=root.group(1),
        suffix=suffix,
        path=_parse_path_segments(suffix),
    )


def _parse_path_segments(suffix: str) -> DataPath | None:
    segments: list[PathSegment] = []
    position = 0
    while position < len(suffix):
        match = _PATH_SEGMENT.match(suffix, position)
        if match is None:
            return None
        if (attribute := match.group("field")) is not None:
            segments.append(attribute)
        elif (index := match.group("index")) is not None:
            segments.append(int(index))
        elif (quoted := match.group("quoted")) is not None:
            value = ast.literal_eval(quoted)
            if not isinstance(value, str):
                return None
            segments.append(value)
        position = match.end()
    return tuple(segments)


def _direct_input_ref(tree: Tree[Token]) -> _InputRef | None:
    """Reference for an expression that is exactly one ``inputs.*`` lookup."""
    if tree.data != "template_action_inputs":
        return None
    token = tree.children[0]
    if not isinstance(token, Token):
        raise TracecatExpressionError("Expected template input path token")
    return _parse_input_ref(str(token))


def _template_input_ref(template: str) -> _InputRef | None:
    """Reference for a template string that is exactly one ``inputs.*`` lookup."""
    if (expression := _standalone_expression(template)) is None:
        return None
    return _direct_input_ref(_parse_expression(expression))


def _standalone_expression(value: str) -> str | None:
    if patterns.STANDALONE_TEMPLATE.match(value) is None:
        return None
    match = patterns.TEMPLATE_STRING.fullmatch(value)
    if match is None or not (expression := match.group("expr")):
        return None
    return expression


def _raise_secret_key_error() -> None:
    raise TracecatExpressionError(
        "Secret expressions are not allowed in dictionary keys",
        detail={"code": "secret_expression_in_key"},
    )
