from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from enum import StrEnum, auto
from typing import Any


class TracecatEnum(StrEnum):
    def __repr__(self) -> str:
        return str(self)


class ExprContext(TracecatEnum):
    """Global contexts"""

    ACTIONS = "ACTIONS"
    SECRETS = "SECRETS"
    FN = "FN"
    VARS = "VARS"
    ENV = "ENV"
    TRIGGER = "TRIGGER"

    """Action-local variables"""
    LOCAL_VARS = "var"
    TEMPLATE_ACTION_INPUTS = "inputs"
    TEMPLATE_ACTION_STEPS = "steps"


class ExprType(TracecatEnum):
    GENERIC = auto()
    ACTION = auto()
    SECRET = auto()
    FUNCTION = auto()
    VARS = auto()
    ENV = auto()
    LOCAL_VARS = auto()
    LITERAL = auto()
    TYPECAST = auto()
    ITERATOR = auto()
    TERNARY = auto()
    TRIGGER = auto()


VISITOR_NODE_TO_EXPR_TYPE = {
    "expression": ExprType.GENERIC,
    "actions": ExprType.ACTION,
    "secrets": ExprType.SECRET,
    "function": ExprType.FUNCTION,
    "variables": ExprType.VARS,
    "env": ExprType.ENV,
    "local_vars": ExprType.LOCAL_VARS,
    "literal": ExprType.LITERAL,
    "typecast": ExprType.TYPECAST,
    "iterator": ExprType.ITERATOR,
    "ternary": ExprType.TERNARY,
    "trigger": ExprType.TRIGGER,
}


ExprContextType = dict[ExprContext, Any]


@dataclass
class IterableExpr[T]:
    """An expression that represents an iterable collection."""

    iterator: str
    collection: Iterable[T]

    def __iter__(self) -> Iterator[tuple[str, T]]:
        for item in self.collection:
            yield self.iterator, item
