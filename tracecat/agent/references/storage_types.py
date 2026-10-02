"""Typed JSON positions stored in direct-reference projections."""

from typing import TypedDict


class ReferencePosition(TypedDict):
    line: int
    column: int
