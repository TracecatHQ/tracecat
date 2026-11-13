"""Domain types for saved case views."""

from typing import Literal

type BuiltinCaseViewId = Literal[
    "all",
    "assigned_to_me",
    "unassigned",
    "created_by_me",
]
"""Stable IDs of the views defined in code. They are never stored as rows."""

BUILTIN_CASE_VIEW_IDS: tuple[BuiltinCaseViewId, ...] = (
    "all",
    "assigned_to_me",
    "unassigned",
    "created_by_me",
)
