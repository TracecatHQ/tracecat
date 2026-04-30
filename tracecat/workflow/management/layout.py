"""Auto-layout helpers for workflow graphs."""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from typing import NotRequired, TypedDict


class WorkflowActionLayoutInput(TypedDict):
    """Minimal action shape needed to auto-generate workflow layout."""

    ref: str
    depends_on: NotRequired[list[str]]


class GeneratedLayoutPoint(TypedDict):
    """Generated x/y layout coordinates."""

    x: float
    y: float


class GeneratedLayoutAction(GeneratedLayoutPoint):
    """Generated action layout coordinates."""

    ref: str


class GeneratedWorkflowLayout(TypedDict):
    """Generated workflow layout payload."""

    trigger: GeneratedLayoutPoint
    actions: list[GeneratedLayoutAction]


NODE_HEIGHT = 300
NODE_WIDTH = 300


def auto_generate_layout(
    actions: Sequence[WorkflowActionLayoutInput],
) -> GeneratedWorkflowLayout:
    """Generate a top-down layout for workflow actions when none is provided.

    Walks the dependency graph to assign each action a depth (row), then
    spreads siblings horizontally. The trigger node sits at the top.
    """
    dependents: dict[str, list[str]] = {a["ref"]: [] for a in actions}
    deps: dict[str, list[str]] = {}
    for a in actions:
        deps[a["ref"]] = a.get("depends_on", []) or []
        for dep in deps[a["ref"]]:
            src = dep.split(".", 1)[0]
            if src in dependents:
                dependents[src].append(a["ref"])

    depth: dict[str, int] = {}
    roots = [ref for ref, d in deps.items() if not d]
    if not roots:
        for i, a in enumerate(actions):
            depth[a["ref"]] = i
    else:
        queue = deque(roots)
        max_depth = max(len(actions) - 1, 0)
        for r in roots:
            depth[r] = 0
        while queue:
            ref = queue.popleft()
            for child in dependents.get(ref, []):
                new_depth = depth[ref] + 1
                if new_depth > max_depth:
                    continue
                if child not in depth or new_depth > depth[child]:
                    depth[child] = new_depth
                    queue.append(child)
    if len(depth) < len(actions):
        next_depth = max(depth.values(), default=-1) + 1
        for action in actions:
            ref = action["ref"]
            if ref not in depth:
                depth[ref] = next_depth
                next_depth += 1

    rows: dict[int, list[str]] = {}
    for ref, d in depth.items():
        rows.setdefault(d, []).append(ref)

    for d in rows:
        rows[d].sort()

    layout: GeneratedWorkflowLayout = {
        "trigger": {"x": 0, "y": 0},
        "actions": [],
    }
    for d in sorted(rows.keys()):
        refs_in_row = rows[d]
        total_width = (len(refs_in_row) - 1) * NODE_WIDTH
        start_x = -total_width / 2
        y = (d + 1) * NODE_HEIGHT
        for i, ref in enumerate(refs_in_row):
            layout["actions"].append(
                {
                    "ref": ref,
                    "x": start_x + i * NODE_WIDTH,
                    "y": y,
                }
            )

    return layout
