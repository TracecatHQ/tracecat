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
        lines.append("join: any")
    if action.run_if is not None:
        lines.append(f"if: {_strip_template(action.run_if)}")
    return "<br/>".join(_escape_label(line) for line in lines)


def _ancestors(
    ref: str,
    parents: Mapping[str, Sequence[str]],
) -> list[str]:
    seen: set[str] = set()
    stack = list(parents.get(ref, []))
    ordered: list[str] = []
    while stack:
        current = stack.pop()
        if current in seen or current == ref:
            continue
        seen.add(current)
        ordered.append(current)
        stack.extend(parents.get(current, []))
    return ordered


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
            gated_by=sorted(
                ancestor
                for ancestor in _ancestors(action.ref, parents)
                if ancestor in conditional
            ),
        )
        for action in actions
    ]
    return "\n".join(lines), graph_actions
