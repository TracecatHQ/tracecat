"""Incremental literal substring matching for invocation-local secret masks."""

from __future__ import annotations

from array import array
from bisect import bisect_left
from collections import deque
from dataclasses import dataclass, field


def _build_transitions(
    patterns: set[str],
) -> tuple[array[int], array[int], array[int], bytearray]:
    """Build sorted adjacency arrays without allocating a dictionary per state.

    Lexically adjacent patterns reuse their common prefix. Packed child/sibling
    links let us append new branches without searching or moving existing edges.
    Flatten those links into contiguous, sorted transitions for binary lookup.
    State zero is the root and also denotes a missing transition.
    """
    labels = array("I", [0])
    first_child = array("I", [0])
    sibling = array("I", [0])
    matched = bytearray(1)
    path = array("I", [0])
    previous = ""
    for pattern in sorted(patterns):
        shared = 0
        for left, right in zip(previous, pattern, strict=False):
            if left != right:
                break
            shared += 1
        del path[shared + 1 :]
        for char in pattern[shared:]:
            parent = path[-1]
            state = len(labels)
            labels.append(ord(char))
            sibling.append(first_child[parent])
            first_child.append(0)
            first_child[parent] = state
            matched.append(0)
            path.append(state)
        matched[path[-1]] = 1
        previous = pattern
    del path

    offsets = array("I", [0])
    characters = array("I")
    targets = array("I")
    for child in first_child:
        children: list[int] = []
        while child:
            children.append(child)
            child = sibling[child]
        # Children were prepended in lexical order, so reverse the sibling list.
        for child in reversed(children):
            characters.append(labels[child])
            targets.append(child)
        offsets.append(len(targets))
    return offsets, characters, targets, matched


class _Automaton:
    """Aho-Corasick matcher with packed Unicode transitions and state metadata."""

    def __init__(self, patterns: set[str]) -> None:
        self.patterns = patterns
        self._offsets, self._characters, self._targets, self._matched = (
            _build_transitions(patterns)
        )
        self._fail = array("I", [0]) * len(self._matched)
        # Most misses return to the root. One small dictionary avoids binary
        # search there without paying for a dictionary on every state.
        self._root = {
            self._characters[edge]: self._targets[edge]
            for edge in range(self._offsets[1])
        }
        queue = deque(self._root.values())
        while queue:
            state = queue.popleft()
            for edge in range(self._offsets[state], self._offsets[state + 1]):
                char, child = self._characters[edge], self._targets[edge]
                queue.append(child)
                fallback = self._fail[state]
                target = self._transition(fallback, char)
                while fallback and not target:
                    fallback = self._fail[fallback]
                    target = self._transition(fallback, char)
                self._fail[child] = target
                self._matched[child] |= self._matched[self._fail[child]]

    def _transition(self, state: int, char: int) -> int:
        if not state:
            return self._root.get(char, 0)
        start, end = self._offsets[state], self._offsets[state + 1]
        edge = bisect_left(self._characters, char, start, end)
        if edge < end and self._characters[edge] == char:
            return self._targets[edge]
        return 0

    def contains(self, text: str) -> bool:
        state = 0
        for char in text:
            codepoint = ord(char)
            target = self._transition(state, codepoint)
            while state and not target:
                state = self._fail[state]
                target = self._transition(state, codepoint)
            state = target
            if self._matched[state]:
                return True
        return False


@dataclass(slots=True, repr=False)
class SecretSubstringIndex:
    """Search literal masks without scanning the mask set for each value.

    New masks are batched until a substring lookup needs them. Immutable
    matchers occupy size tiers, with at most one matcher per power of two.
    Merging equal tiers prevents a stream of observe/query pairs from rebuilding
    the entire index each time. Lookups scan the text once per occupied tier.
    The caller adds each nonempty mask only once.
    """

    _pending: set[str] = field(default_factory=set)
    _tiers: dict[int, _Automaton] = field(default_factory=dict)

    def add(self, value: str) -> None:
        """Queue a new, nonempty literal mask."""
        self._pending.add(value)

    def contains(self, text: str) -> bool:
        """Return whether any indexed mask occurs in the text."""
        if self._pending:
            patterns, self._pending = self._pending, set()
            tier = len(patterns).bit_length() - 1
            while previous := self._tiers.pop(tier, None):
                patterns.update(previous.patterns)
                tier = len(patterns).bit_length() - 1
            self._tiers[tier] = _Automaton(patterns)
        return any(matcher.contains(text) for matcher in self._tiers.values())
