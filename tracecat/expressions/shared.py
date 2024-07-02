from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from enum import StrEnum, auto
from typing import Any


class ExprContext(StrEnum):
    ACTIONS = "ACTIONS"
    SECRETS = "SECRETS"
    FN = "FN"
    INPUTS = "INPUTS"
    ENV = "ENV"
    TRIGGER = "TRIGGER"
    LOCAL_VARS = "var"  # Action-local variables


class ExprType(StrEnum):
    GENERIC = auto()
    ACTION = auto()
    SECRET = auto()
    FUNCTION = auto()
    INPUT = auto()
    ENV = auto()
    LOCAL_VARS = auto()
    LITERAL = auto()
    TYPECAST = auto()
    ITERATOR = auto()
    TERNARY = auto()
    TRIGGER = auto()

    def __repr__(self) -> str:
        return str(self)


ExprContextType = dict[ExprContext, Any]


@dataclass
class IterableExpr[T]:
    """An expression that represents an iterable collection."""

    iterator: str
    collection: Iterable[T]

    def __iter__(self) -> Iterator[tuple[str, T]]:
        for item in self.collection:
            yield self.iterator, item
