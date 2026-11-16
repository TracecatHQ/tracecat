"""Domain types for saved case views."""

from typing import Literal, get_args

type BuiltinCaseViewId = Literal["assigned_to_me"]
"""Stable IDs of the views defined in code. They are never stored as rows."""

BUILTIN_CASE_VIEW_IDS: tuple[BuiltinCaseViewId, ...] = get_args(
    BuiltinCaseViewId.__value__
)
