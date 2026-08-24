"""Structured textual diffs for immutable case versions."""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from tracecat.cases.enums import CaseVersionDiffOperation
from tracecat.cases.versions.schemas import (
    CaseVersionDiffRead,
    CaseVersionDiffSegmentRead,
)

_WORD_DIFF_TOKEN_PATTERN = re.compile(r"\s+|\w+|[^\w\s]+")
# Bound SequenceMatcher's quadratic worst case; larger inputs use an exact
# whole-content replacement diff.
_MAX_SEQUENCE_MATCHER_TOKEN_PAIRS = 1_000_000


def compute_case_version_diff(
    predecessor: str,
    selected: str,
) -> CaseVersionDiffRead:
    """Build a word-level edit script from predecessor to selected content."""
    segments: list[CaseVersionDiffSegmentRead] = []

    def append_segment(operation: CaseVersionDiffOperation, text: str) -> None:
        if not text:
            return
        if segments and segments[-1].operation == operation:
            previous = segments[-1]
            segments[-1] = CaseVersionDiffSegmentRead(
                operation=operation,
                text=previous.text + text,
            )
            return
        segments.append(CaseVersionDiffSegmentRead(operation=operation, text=text))

    if predecessor == selected:
        append_segment(CaseVersionDiffOperation.EQUAL, selected)
        return CaseVersionDiffRead(changed=False, segments=segments)

    predecessor_tokens = _WORD_DIFF_TOKEN_PATTERN.findall(predecessor)
    selected_tokens = _WORD_DIFF_TOKEN_PATTERN.findall(selected)
    if (
        len(predecessor_tokens) * len(selected_tokens)
        > _MAX_SEQUENCE_MATCHER_TOKEN_PAIRS
    ):
        append_segment(CaseVersionDiffOperation.DELETE, predecessor)
        append_segment(CaseVersionDiffOperation.INSERT, selected)
        return CaseVersionDiffRead(changed=True, segments=segments)

    matcher = SequenceMatcher(
        a=predecessor_tokens,
        b=selected_tokens,
    )

    for (
        tag,
        predecessor_start,
        predecessor_end,
        selected_start,
        selected_end,
    ) in matcher.get_opcodes():
        predecessor_text = "".join(
            predecessor_tokens[predecessor_start:predecessor_end]
        )
        selected_text = "".join(selected_tokens[selected_start:selected_end])
        match tag:
            case "equal":
                append_segment(CaseVersionDiffOperation.EQUAL, selected_text)
            case "delete":
                append_segment(CaseVersionDiffOperation.DELETE, predecessor_text)
            case "insert":
                append_segment(CaseVersionDiffOperation.INSERT, selected_text)
            case "replace":
                append_segment(CaseVersionDiffOperation.DELETE, predecessor_text)
                append_segment(CaseVersionDiffOperation.INSERT, selected_text)

    return CaseVersionDiffRead(
        changed=predecessor != selected,
        segments=segments,
    )
