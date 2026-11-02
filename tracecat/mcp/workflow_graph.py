"""Compact control-flow views of workflow graphs for MCP clients."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from tracecat.dsl.enums import JoinStrategy
from tracecat.dsl.schemas import ActionStatement
from tracecat.mcp.schemas import WorkflowGraphAction

_TRIGGER_NODE_ID = "trigger"
_MERMAID_RESERVED_IDS = frozenset(
    {
        "class",
        "classdef",
        "click",
        "direction",
        "end",
        "flowchart",
        "graph",
        "linkstyle",
        "style",
        "subgraph",
        _TRIGGER_NODE_ID,
    }
)


def _node_id(ref: str) -> str:
    return f"{ref}_" if ref.lower() in _MERMAID_RESERVED_IDS else ref


def _strip_template(expression: str) -> str:
    stripped = expression.strip()
    if stripped.startswith("${{") and stripped.endswith("}}"):
        return stripped[3:-2].strip()
    return stripped


def _escape_label(text: str) -> str:
    return (
        text.replace("&", "#amp;")
        .replace('"', "#quot;")
        .replace("<", "#lt;")
        .replace(">", "#gt;")
        .replace("\n", " ")
    )


def _split_dependency(dependency: str) -> tuple[str, str]:
    source, _, handle = dependency.partition(".")
    return source, handle or "success"


def _node_label(action: ActionStatement) -> str:
    lines = [action.ref, action.action]
    if action.for_each is not None:
        loops = (
            action.for_each if isinstance(action.for_each, list) else [action.for_each]
        )
        lines.extend(f"for_each: {_strip_template(loop)}" for loop in loops)
    if action.join_strategy == JoinStrategy.ANY:
        lines.append("join: any (one parent is enough)")
    if action.run_if is not None:
        lines.append(f"if: {_strip_template(action.run_if)}")
    return "<br/>".join(_escape_label(line) for line in lines)


def _upstream_gates(
    actions: Sequence[ActionStatement],
    parents: Mapping[str, Sequence[str]],
    conditional: set[str],
) -> dict[str, set[str]]:
    """Map each ref to the conditional ancestors that must have run for it to run.

    A single-parent action or an ``all`` join inherits every parent's gates. An
    ``any`` join runs when one parent succeeds, so it keeps only the gates
    shared by all of its parents.
    """
    join_any = {
        action.ref for action in actions if action.join_strategy == JoinStrategy.ANY
    }
    gates: dict[str, set[str]] = {}

    def resolve(ref: str, visiting: frozenset[str]) -> set[str]:
        if ref in gates:
            return gates[ref]
        parent_gates: list[set[str]] = []
        for parent in parents.get(ref, []):
            if parent in visiting:
                continue
            inherited = resolve(parent, visiting | {ref})
            own = {parent} if parent in conditional else set()
            parent_gates.append(inherited | own)
        if not parent_gates:
            result: set[str] = set()
        elif ref in join_any:
            result = set.intersection(*parent_gates)
        else:
            result = set.union(*parent_gates)
        gates[ref] = result
        return result

    for action in actions:
        resolve(action.ref, frozenset())
    return gates


def build_workflow_graph(
    actions: Sequence[ActionStatement],
) -> tuple[str, list[WorkflowGraphAction]]:
    """Render actions as a Mermaid flowchart and list each action's upstream gates.

    Nodes show the ref, action type, loops, join strategy, and ``run_if``.
    Error-path edges are dotted and labelled ``error``. Arguments are omitted.
    """
    refs = {action.ref for action in actions}
    parents: dict[str, list[str]] = {}
    for action in actions:
        parents[action.ref] = [
            source
            for source, _ in map(_split_dependency, action.depends_on)
            if source in refs
        ]
    conditional = {action.ref for action in actions if action.run_if is not None}
    gates = _upstream_gates(actions, parents, conditional)

    lines = ["flowchart TD", f"  {_TRIGGER_NODE_ID}([trigger])"]
    for action in actions:
        lines.append(f'  {_node_id(action.ref)}["{_node_label(action)}"]')
    for action in actions:
        target = _node_id(action.ref)
        if not action.depends_on:
            lines.append(f"  {_TRIGGER_NODE_ID} --> {target}")
            continue
        for dependency in action.depends_on:
            source, handle = _split_dependency(dependency)
            if source not in refs:
                continue
            if handle == "error":
                lines.append(f"  {_node_id(source)} -. error .-> {target}")
            else:
                lines.append(f"  {_node_id(source)} --> {target}")

    graph_actions = [
        WorkflowGraphAction(
            ref=action.ref,
            depends_on=list(action.depends_on),
            run_if=action.run_if,
            join_strategy=action.join_strategy.value,
            gated_by=sorted(gates[action.ref]),
        )
        for action in actions
    ]
    return "\n".join(lines), graph_actions
