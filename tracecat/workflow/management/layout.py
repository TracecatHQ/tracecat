"""Auto-layout helpers for workflow graphs."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
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
"""Vertical distance between the tops of consecutive rows."""

NODE_WIDTH = 256
"""Rendered width of a builder node (matches the frontend ``w-64``)."""

NODE_HORIZONTAL_GAP = 250
"""Horizontal gap between sibling nodes (matches the frontend dagre ``nodesep``)."""

COLUMN_WIDTH = NODE_WIDTH + NODE_HORIZONTAL_GAP

_ORDERING_SWEEPS = 8
_UNANCHORED_WEIGHT = 1e-3


@dataclass(slots=True)
class _Block:
    weight: float
    weighted_sum: float
    size: int

    @property
    def value(self) -> float:
        return self.weighted_sum / self.weight


def _dependency_source(dependency: str) -> str:
    return dependency.split(".", 1)[0]


def _is_error_dependency(dependency: str) -> bool:
    return dependency.partition(".")[2] == "error"


def _edge_rank(is_error: set[bool]) -> int:
    if len(is_error) > 1:
        return 1
    if True in is_error:
        return 2
    return 0


def _build_branch_ranks(
    actions: Sequence[WorkflowActionLayoutInput],
    parents: Mapping[str, Sequence[str]],
) -> dict[str, float]:
    """Rank each action by the path kind it takes from its parents.

    Ranks are ``0`` for success-only, ``1`` for success and error, and ``2``
    for error-only edges, averaged over parents, so siblings read left to
    right as success, success and error, then error.
    """
    ranks: dict[str, float] = {}
    for action in actions:
        ref = action["ref"]
        kinds: dict[str, set[bool]] = {source: set() for source in parents[ref]}
        for dependency in action.get("depends_on", []) or []:
            source = _dependency_source(dependency)
            if source in kinds:
                kinds[source].add(_is_error_dependency(dependency))
        edge_ranks = [_edge_rank(flags) for flags in kinds.values()]
        ranks[ref] = sum(edge_ranks) / len(edge_ranks) if edge_ranks else 0.0
    return ranks


def _build_parents(
    actions: Sequence[WorkflowActionLayoutInput],
) -> dict[str, list[str]]:
    refs = {action["ref"] for action in actions}
    parents: dict[str, list[str]] = {}
    for action in actions:
        ref = action["ref"]
        seen: list[str] = []
        for dependency in action.get("depends_on", []) or []:
            source = _dependency_source(dependency)
            if source in refs and source != ref and source not in seen:
                seen.append(source)
        parents[ref] = seen
    return parents


def _assign_depths(
    refs: Sequence[str],
    parents: Mapping[str, Sequence[str]],
    children: Mapping[str, Sequence[str]],
) -> dict[str, int]:
    """Assign each action the length of its longest path from a root.

    Depth is capped at ``len(refs) - 1`` so cycles terminate. Actions that are
    only reachable through a cycle are appended below the deepest row.
    """
    depth: dict[str, int] = {}
    roots = [ref for ref in refs if not parents[ref]]
    max_depth = max(len(refs) - 1, 0)
    queue = deque(roots)
    for root in roots:
        depth[root] = 0
    while queue:
        ref = queue.popleft()
        for child in children[ref]:
            new_depth = depth[ref] + 1
            if new_depth > max_depth:
                continue
            if child not in depth or new_depth > depth[child]:
                depth[child] = new_depth
                queue.append(child)

    next_depth = max(depth.values(), default=-1) + 1
    for ref in refs:
        if ref not in depth:
            depth[ref] = next_depth
            next_depth += 1
    return depth


def _initial_rows(
    refs: Sequence[str],
    depth: Mapping[str, int],
    children: Mapping[str, Sequence[str]],
    branch_rank: Mapping[str, float],
) -> list[list[str]]:
    """Order each row by a depth-first walk so each branch stays contiguous.

    Children are visited success paths first and error paths last.
    """
    visited: set[str] = set()
    walk: list[str] = []
    for start in refs:
        if start in visited:
            continue
        stack = [start]
        while stack:
            ref = stack.pop()
            if ref in visited:
                continue
            visited.add(ref)
            walk.append(ref)
            ordered = sorted(children[ref], key=lambda child: branch_rank[child])
            stack.extend(reversed(ordered))

    row_count = max(depth.values(), default=-1) + 1
    rows: list[list[str]] = [[] for _ in range(row_count)]
    for ref in walk:
        rows[depth[ref]].append(ref)
    return rows


def _centered_positions(rows: Sequence[Sequence[str]]) -> dict[str, float]:
    positions: dict[str, float] = {}
    for row in rows:
        offset = (len(row) - 1) / 2
        for index, ref in enumerate(row):
            positions[ref] = index - offset
    return positions


def _count_crossings(
    rows: Sequence[Sequence[str]],
    depth: Mapping[str, int],
    parents: Mapping[str, Sequence[str]],
) -> int:
    """Count crossings between edges that span the same pair of rows."""
    index = {ref: i for row in rows for i, ref in enumerate(row)}
    edges_by_span: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for ref, sources in parents.items():
        for source in sources:
            span = (depth[source], depth[ref])
            edges_by_span.setdefault(span, []).append((index[source], index[ref]))

    crossings = 0
    for edges in edges_by_span.values():
        for i, (a_source, a_target) in enumerate(edges):
            for b_source, b_target in edges[i + 1 :]:
                if (a_source - b_source) * (a_target - b_target) < 0:
                    crossings += 1
    return crossings


def _reorder_rows(
    rows: list[list[str]],
    neighbors: Mapping[str, Sequence[str]],
    row_indices: Sequence[int],
    branch_rank: Mapping[str, float],
) -> list[list[str]]:
    """Sort rows by the barycenter of each node's neighbors.

    Ties keep success paths left of error paths, then the current order.
    """
    rows = [list(row) for row in rows]
    for row_index in row_indices:
        positions = _centered_positions(rows)
        row = rows[row_index]
        keyed: list[tuple[float, float, int, str]] = []
        for index, ref in enumerate(row):
            linked = neighbors[ref]
            if linked:
                key = sum(positions[other] for other in linked) / len(linked)
            else:
                key = positions[ref]
            keyed.append((key, branch_rank[ref], index, ref))
        rows[row_index] = [ref for *_, ref in sorted(keyed)]
    return rows


def _order_rows(
    rows: list[list[str]],
    depth: Mapping[str, int],
    parents: Mapping[str, Sequence[str]],
    children: Mapping[str, Sequence[str]],
    branch_rank: Mapping[str, float],
) -> list[list[str]]:
    """Reduce edge crossings with alternating barycenter sweeps."""
    best_rows = rows
    best_crossings = _count_crossings(rows, depth, parents)
    current = rows
    down = list(range(1, len(rows)))
    up = list(range(len(rows) - 2, -1, -1))
    for sweep in range(_ORDERING_SWEEPS):
        if best_crossings == 0:
            break
        if sweep % 2 == 0:
            current = _reorder_rows(current, parents, down, branch_rank)
        else:
            current = _reorder_rows(current, children, up, branch_rank)
        crossings = _count_crossings(current, depth, parents)
        if crossings < best_crossings:
            best_rows = current
            best_crossings = crossings
    return best_rows


def _separated_fit(targets: Sequence[float], weights: Sequence[float]) -> list[float]:
    """Fit ordered positions to targets while keeping them one column apart.

    Minimizes ``sum(w_i * (x_i - t_i) ** 2)`` subject to
    ``x_{i+1} - x_i >= 1`` with weighted pool-adjacent-violators.
    """
    blocks: list[_Block] = []
    for index, (target, weight) in enumerate(zip(targets, weights, strict=True)):
        blocks.append(_Block(weight, weight * (target - index), 1))
        while len(blocks) > 1 and blocks[-2].value > blocks[-1].value:
            last = blocks.pop()
            blocks[-1].weight += last.weight
            blocks[-1].weighted_sum += last.weighted_sum
            blocks[-1].size += last.size

    positions: list[float] = []
    for block in blocks:
        for _ in range(block.size):
            positions.append(block.value + len(positions))
    return positions


def _place_row(
    row: Sequence[str],
    anchors: Mapping[str, Sequence[str]],
    x: dict[str, float],
) -> None:
    """Place a row under (or over) its anchors without overlapping siblings."""
    targets: list[float | None] = []
    for ref in row:
        linked = [other for other in anchors[ref] if other in x]
        targets.append(sum(x[o] for o in linked) / len(linked) if linked else None)

    if all(target is None for target in targets):
        offset = (len(row) - 1) / 2
        filled = [index - offset for index in range(len(row))]
        weights = [1.0] * len(row)
    else:
        filled: list[float] = []
        weights: list[float] = []
        previous: float | None = None
        for index, target in enumerate(targets):
            if target is not None:
                previous = target
                filled.append(target)
                weights.append(1.0)
                continue
            if previous is None:
                following = next(t for t in targets[index:] if t is not None)
                gap = next(i for i, t in enumerate(targets[index:]) if t is not None)
                filled.append(following - gap)
            else:
                filled.append(previous + 1)
                previous += 1
            weights.append(_UNANCHORED_WEIGHT)

    for ref, position in zip(row, _separated_fit(filled, weights), strict=True):
        x[ref] = position


def _assign_columns(
    rows: Sequence[Sequence[str]],
    parents: Mapping[str, Sequence[str]],
    children: Mapping[str, Sequence[str]],
) -> dict[str, float]:
    """Center children under parents, then parents over children, then settle."""
    x: dict[str, float] = {}
    passes: list[tuple[Sequence[Sequence[str]], Mapping[str, Sequence[str]]]] = [
        (rows, parents),
        (list(reversed(rows)), children),
        (rows, parents),
    ]
    for ordered_rows, anchors in passes:
        with_fallback = _AnchorsWithFallback(anchors, x)
        for row in ordered_rows:
            _place_row(row, with_fallback, x)
    return x


class _AnchorsWithFallback(Mapping[str, Sequence[str]]):
    """Anchors that fall back to a node's own current column."""

    def __init__(
        self,
        anchors: Mapping[str, Sequence[str]],
        current: Mapping[str, float],
    ) -> None:
        self._anchors = anchors
        self._current = current

    def __getitem__(self, ref: str) -> Sequence[str]:
        linked = self._anchors[ref]
        if linked:
            return linked
        return [ref] if ref in self._current else []

    def __iter__(self) -> Iterator[str]:
        return iter(self._anchors)

    def __len__(self) -> int:
        return len(self._anchors)


def auto_generate_layout(
    actions: Sequence[WorkflowActionLayoutInput],
) -> GeneratedWorkflowLayout:
    """Generate a readable top-down layout for workflow actions.

    Rows follow the longest dependency path so each action sits below every
    action it waits for. Within a row, siblings are ordered to reduce edge
    crossings and each action is centered under its parents, so a linear chain
    is a straight vertical line and a fan-out is centered under its source.
    Siblings read left to right as success, success and error, then error.
    The trigger sits at the origin, centered over the root actions.
    """
    refs = [action["ref"] for action in actions]
    parents = _build_parents(actions)
    children: dict[str, list[str]] = {ref: [] for ref in refs}
    for ref in refs:
        for source in parents[ref]:
            children[source].append(ref)

    branch_rank = _build_branch_ranks(actions, parents)
    depth = _assign_depths(refs, parents, children)
    rows = _initial_rows(refs, depth, children, branch_rank)
    rows = _order_rows(rows, depth, parents, children, branch_rank)
    columns = _assign_columns(rows, parents, children)

    roots = rows[0] if rows else []
    origin = sum(columns[ref] for ref in roots) / len(roots) if roots else 0.0

    layout: GeneratedWorkflowLayout = {"trigger": {"x": 0, "y": 0}, "actions": []}
    for row_index, row in enumerate(rows):
        for ref in row:
            layout["actions"].append(
                {
                    "ref": ref,
                    "x": float(round((columns[ref] - origin) * COLUMN_WIDTH)),
                    "y": float((row_index + 1) * NODE_HEIGHT),
                }
            )
    return layout


def graph_requires_relayout(
    previous: Sequence[WorkflowActionLayoutInput],
    updated: Sequence[WorkflowActionLayoutInput],
) -> bool:
    """Return whether a graph edit adds actions or rewires surviving ones.

    Removing an action without touching any other ``depends_on`` leaves the
    remaining positions readable, so it does not require a new layout.
    """
    previous_dependencies = {
        action["ref"]: set(action.get("depends_on", []) or []) for action in previous
    }
    for action in updated:
        dependencies = previous_dependencies.get(action["ref"])
        if dependencies is None:
            return True
        if set(action.get("depends_on", []) or []) != dependencies:
            return True
    return False
