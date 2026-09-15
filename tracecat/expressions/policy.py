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
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

from lark import Token, Tree

from tracecat.exceptions import TracecatExpressionError
from tracecat.expressions import patterns
from tracecat.expressions.common import eval_jsonpath
from tracecat.expressions.eval import eval_templated_object
from tracecat.expressions.parser.core import parser
from tracecat.parse import traverse_expressions
from tracecat.secrets.constants import MASK_VALUE

__all__ = (
    "ActionArgumentPlan",
    "ExpressionPolicy",
    "ProvenanceMap",
    "TaintState",
    "build_provenance",
    "expression_policy",
    "references_secret_derived_value",
    "resolve_action_args",
)

type PathSegment = str | int
type DataPath = tuple[PathSegment, ...]

_PATH_SEGMENT = re.compile(
    r"""
    (?:
        \.(?:
            (?P<field>[A-Za-z_][A-Za-z0-9_]*)
            |(?P<dot_quoted>"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')
        )
        |\[(?P<index>-?\d+)\]
        |\[(?P<quoted>"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')\]
    )
    """,
    re.VERBOSE,
)


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
    "core.cases.create_task": frozenset(
        {"title", "description", "default_trigger_values"}
    ),
    "core.cases.update_task": frozenset(
        {"title", "description", "default_trigger_values"}
    ),
    "core.cases.add_case_tag": frozenset({"tag"}),
    "core.cases.upload_attachment": frozenset(
        {"file_name", "content_base64", "content_type"}
    ),
    "core.cases.upload_attachment_from_url": frozenset({"file_name"}),
    "core.table.create_table": frozenset({"name", "columns"}),
    "core.table.update_table": frozenset({"new_name"}),
    "core.table.create_column": frozenset({"column"}),
    "core.table.update_column": frozenset({"update"}),
    "core.table.insert_row": frozenset({"row_data"}),
    "core.table.insert_rows": frozenset({"rows_data"}),
    "core.table.update_row": frozenset({"row_data"}),
    "core.cases.insert_row": frozenset({"row"}),
    "ai.agent.create_preset": frozenset(
        {
            "instructions",
            "name",
            "description",
            "slug",
            "model_name",
            "model_provider",
            "catalog_id",
            "base_url",
            "output_type",
            "actions",
            "namespaces",
            "tool_approvals",
            "mcp_integrations",
            "agents",
            "skills",
        }
    ),
    "ai.agent.update_preset": frozenset(
        {
            "instructions",
            "name",
            "description",
            "new_slug",
            "model_name",
            "model_provider",
            "catalog_id",
            "base_url",
            "output_type",
            "actions",
            "namespaces",
            "tool_approvals",
            "mcp_integrations",
            "agents",
            "skills",
        }
    ),
    "ai.skill.create_skill": frozenset({"name", "description"}),
    "ai.skill.publish_skill_version": frozenset({"files"}),
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

    tainted: bool = False
    """Authored arg referenced a runtime carrier; gates error text only, never masking."""


type ProvenanceMap = Mapping[str, _InputProvenance]


@dataclass(frozen=True, slots=True)
class TaintState:
    """Secret taint known at a template invocation's evaluation boundary.

    One lookup table per runtime-born namespace:

    - ``provenance`` answers "is ``inputs.x`` secret-backed?" — decided by the
      caller's authored args, fixed before the run starts.
    - ``tainted_steps`` answers "is ``steps.x`` secret-derived?" — decided step
      by step while the template runs.

    Provenance also seeds step taint: a step referencing ``inputs.*`` is
    tainted only if that input is. Without it every input-touching step would
    cascade to conservative, and the failure gate would coarsen back to
    withholding every template error.
    """

    provenance: ProvenanceMap | None = None
    """Authored input sources and their secret dependencies, when known."""

    tainted_steps: frozenset[str] = frozenset()
    """Refs of prior template steps whose arguments are secret-dependent."""

    @property
    def tainted_inputs(self) -> frozenset[str]:
        """Parameters whose authored argument referenced a runtime carrier."""
        if self.provenance is None:
            return frozenset()
        return frozenset(
            parameter
            for parameter, binding in self.provenance.items()
            if binding.tainted
        )

    def after_step(
        self, ref: str, args: Any, *, action_reaches_secrets: bool = False
    ) -> TaintState:
        """The taint state after this step ran: ``ref`` is marked iff its args are
        secret-dependent or its action can reach secrets. Encapsulates
        classify-then-mark so callers cannot reorder it.

        ``action_reaches_secrets`` covers secrets that never appear in authored
        args: a registry action declaring its own secrets receives them through
        the environment sandbox, so its result is secret-derived even when the
        args are literals.
        """
        if action_reaches_secrets or self.step_args_are_secret_dependent(args):
            return replace(self, tainted_steps=self.tainted_steps | {ref})
        return self

    def step_args_are_secret_dependent(self, args: Any) -> bool:
        """Whether a template step's authored arguments depend on a secret.

        Fails closed on anything ambiguous, so an unparseable argument taints
        the step rather than silently clearing it.
        """
        try:
            for expression in traverse_expressions(args):
                tree = parser.parse(expression)
                if references_secret_derived_value(tree, taint=self):
                    return True
            return False
        except Exception:
            return True

    def step_ref_is_tainted(self, tree: Tree[Token]) -> bool:
        """Whether any ``steps.*`` reference resolves to a tainted step.

        Fails closed when a step ref cannot be extracted from the reference.
        """
        return _ref_is_tainted(tree, "template_action_steps", self.tainted_steps)

    def input_ref_is_tainted(self, tree: Tree[Token]) -> bool:
        """Whether any ``inputs.*`` reference resolves to a tainted parameter.

        Fails closed when an input ref cannot be extracted from the reference.
        """
        return _ref_is_tainted(tree, "template_action_inputs", self.tainted_inputs)


def _ref_is_tainted(tree: Tree[Token], rule: str, tainted: Collection[str]) -> bool:
    """Whether any ``rule`` reference names a tainted root, failing closed."""
    for node in tree.find_data(rule):
        token = node.children[0]
        if not isinstance(token, Token):
            return True
        _, concrete_prefix = _parse_path_segments(str(token))
        if not concrete_prefix:
            return True
        ref = concrete_prefix[0]
        if not isinstance(ref, str) or ref in tainted:
            return True
    return False


def references_secret_derived_value(
    parse_tree: Tree[Token] | None,
    *,
    taint: TaintState | None = None,
) -> bool:
    """Whether the expression references any value that may derive from a secret.

    Reads the parse tree only, never the failing text, so repr() escaping
    cannot defeat it. Runs only after evaluation has already failed. Fails
    closed: whole-expression gating, and any internal error returns True.

    Per namespace:

    - ``SECRETS.*``: always secret.
    - ``inputs.*`` / ``steps.*``: exact via ``taint`` when supplied; assumed
      secret without it. Runtime-carrier taint is carried per parameter, so an
      ``inputs.*`` renamed from a carrier is withheld too.
    - ``ACTIONS.*`` / ``var.*``: always assumed secret. Result masking does not
      clear them — it is conditional on the *current* action's secrets
      (`service.py:1011`), repr-defeatable, and bypassed by AI actions and
      child workflows (`dsl/workflow.py:897`).
    """
    if parse_tree is None:
        return True
    provenance = taint.provenance if taint is not None else None
    try:
        # Always-withheld namespaces; inputs/steps join only when no taint
        # state can resolve them exactly.
        coarse_rules = ["secrets", "actions", "local_vars"]
        if provenance is None:
            coarse_rules.append("template_action_inputs")
        if taint is None:
            coarse_rules.append("template_action_steps")
        for rule in coarse_rules:
            if any(True for _ in parse_tree.find_data(rule)):
                return True
        if taint is not None and taint.step_ref_is_tainted(parse_tree):
            return True
        if taint is not None and taint.input_ref_is_tainted(parse_tree):
            return True
        return _tree_dependencies(parse_tree, provenance).secret
    except Exception:
        return True


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
    taint: TaintState | None = None,
) -> dict[str, _InputProvenance]:
    """Scan authored arguments once and build the template-input mapping."""
    taint = taint or TaintState(provenance=parent)
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
            # ponytail: coarse per parameter; path-sensitive taint if a mixed
            # dict input ever needs it
            tainted=taint.step_args_are_secret_dependent(value),
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
            if not selection.dependencies.secret:
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
            if not selection.source_found:
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
    taint: TaintState | None = None,
) -> dict[str, Any]:
    """Resolve one template step at the target action's policy boundary."""
    redaction_policy = _RedactionPolicy(provenance)
    key_policy = _RedactionPolicy(provenance, reject=True)
    resolved: dict[str, Any] = {}

    for parameter, value in arguments.items():
        match expression_policy(action, parameter):
            case ExpressionPolicy.RESOLVE:
                resolved[parameter] = eval_templated_object(
                    value, operand=context, taint=taint
                )
            case ExpressionPolicy.REDACT_SECRETS:
                resolved[parameter] = eval_templated_object(
                    value,
                    operand=context,
                    policy=redaction_policy,
                    key_policy=key_policy,
                    taint=taint,
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
            if parent is not None and (ref := _template_input_ref(value)) is not None:
                return _select_input(ref, parent).dependencies

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
) -> _InputSelection:
    sources = {parameter: binding.source for parameter, binding in provenance.items()}
    root_dependencies = _SecretDependencies(
        children={
            parameter: binding.dependencies
            for parameter, binding in provenance.items()
            if binding.dependencies.secret
        }
    )

    if ref.path is None:
        dependencies = root_dependencies.select(ref.concrete_prefix).collapsed()
    elif (dependency_path := _normalize_negative_indices(ref.path, sources)) is None:
        dependencies = root_dependencies.collapsed()
    else:
        dependencies = root_dependencies.select(dependency_path)

    try:
        source = eval_jsonpath(
            f"source{ref.selector}",
            {"source": sources},
            strict=True,
        )
    except TracecatExpressionError:
        return _InputSelection(False, None, root_dependencies.collapsed())
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
            collected.append(_select_input(ref, provenance).dependencies)
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
    """One parsed ``inputs<selector>`` reference."""

    selector: str
    """Raw JSONPath selector applied to the authored input mapping."""

    path: DataPath | None
    """Concrete path segments, or None for non-concrete JSONPath selectors."""

    concrete_prefix: DataPath
    """Concrete segments before the first wildcard, filter, or recursive lookup."""


def _parse_input_ref(path: str) -> _InputRef:
    parsed_path, concrete_prefix = _parse_path_segments(path)
    return _InputRef(
        selector=path,
        path=parsed_path,
        concrete_prefix=concrete_prefix,
    )


def _parse_path_segments(suffix: str) -> tuple[DataPath | None, DataPath]:
    segments: list[PathSegment] = []
    position = 0
    while position < len(suffix):
        match = _PATH_SEGMENT.match(suffix, position)
        if match is None:
            return None, tuple(segments)
        if (attribute := match.group("field")) is not None:
            segments.append(attribute)
        elif (index := match.group("index")) is not None:
            segments.append(int(index))
        elif (quoted := match.group("dot_quoted") or match.group("quoted")) is not None:
            value = ast.literal_eval(quoted)
            if not isinstance(value, str) or value == "*":
                return None, tuple(segments)
            segments.append(value)
        position = match.end()
    path = tuple(segments)
    return path, path


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
