import ast
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from enum import StrEnum, auto
from typing import Any, TypeVar
from typing import cast as type_cast

import jsonpath_ng.ext
from jsonpath_ng.exceptions import JsonPathParserError

from tracecat.logger import logger
from tracecat.types.exceptions import TracecatExpressionError


class TracecatEnum(StrEnum):
    def __repr__(self) -> str:
        return str(self)


class ExprContext(TracecatEnum):
    """Expression contexts."""

    # Global contexts
    ACTIONS = "ACTIONS"
    """Actions context"""

    SECRETS = "SECRETS"
    """Secrets context"""

    FN = "FN"
    """Function context"""

    INPUTS = "INPUTS"
    """Inputs context"""

    ENV = "ENV"
    """Environment context"""

    TRIGGER = "TRIGGER"
    """Trigger context"""
    # Action-local variables
    LOCAL_VARS = "var"
    """Action-local variables context"""

    TEMPLATE_ACTION_INPUTS = "inputs"
    """Template action inputs context"""

    TEMPLATE_ACTION_STEPS = "steps"
    """Template action steps context"""


class ExprType(TracecatEnum):
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


VISITOR_NODE_TO_EXPR_TYPE = {
    "expression": ExprType.GENERIC,
    "actions": ExprType.ACTION,
    "secrets": ExprType.SECRET,
    "function": ExprType.FUNCTION,
    "inputs": ExprType.INPUT,
    "env": ExprType.ENV,
    "local_vars": ExprType.LOCAL_VARS,
    "literal": ExprType.LITERAL,
    "typecast": ExprType.TYPECAST,
    "iterator": ExprType.ITERATOR,
    "ternary": ExprType.TERNARY,
    "trigger": ExprType.TRIGGER,
}


@dataclass
class IterableExpr[T]:
    """An expression that represents an iterable collection."""

    iterator: str
    collection: Iterable[T]

    def __iter__(self) -> Iterator[tuple[str, T]]:
        for item in self.collection:
            yield self.iterator, item


K = TypeVar("K", str, StrEnum)
ExprOperand = Mapping[K, Any]


class SafeEvaluator(ast.NodeVisitor):
    RESTRICTED_NODES = {ast.Import, ast.ImportFrom}
    RESTRICTED_SYMBOLS = {
        "eval",
        "import",
        "from",
        "os",
        "sys",
        "exec",
        "locals",
        "globals",
    }
    ALLOWED_FUNCTIONS = {"jsonpath"}

    def visit(self, node):
        if type(node) in self.RESTRICTED_NODES:
            raise ValueError(
                f"Restricted node {type(node).__name__} detected in expression"
            )
        if (
            isinstance(node, ast.Call)
            and (attr := getattr(node.func, "attr", None)) is not None
            and attr in self.RESTRICTED_SYMBOLS
            and attr not in self.ALLOWED_FUNCTIONS
        ):
            raise ValueError(f"Calling restricted functions are not allowed: {attr}")
        self.generic_visit(node)


def _expr_with_context(expr: str, context_type: ExprContext | None) -> str:
    return f"{context_type}.{expr}" if context_type else expr


def build_safe_lambda(lambda_expr: str) -> Callable[[Any], Any]:
    """Build a safe lambda function from a string expression."""
    # Check if the string has any blacklisted symbols
    lambda_expr = lambda_expr.strip()
    if any(
        word in lambda_expr
        for word in SafeEvaluator.RESTRICTED_SYMBOLS - SafeEvaluator.ALLOWED_FUNCTIONS
    ):
        raise ValueError("Expression contains restricted symbols")
    expr_ast = ast.parse(lambda_expr, mode="eval").body

    # Ensure the parsed AST is a comparison or logical expression
    if not isinstance(expr_ast, ast.Lambda):
        raise ValueError("Expression must be a lambda function")

    # Ensure the expression complies with the SafeEvaluator
    SafeEvaluator().visit(expr_ast)

    # Compile the AST node into a code object
    code = compile(ast.Expression(expr_ast), "<string>", "eval")

    # Create a function from the code object with eval_jsonpath in globals
    lambda_func = eval(code, {"jsonpath": eval_jsonpath})
    return type_cast(Callable[[Any], Any], lambda_func)


def eval_jsonpath(
    expr: str,
    operand: Mapping[str | StrEnum, Any],
    *,
    context_type: ExprContext | None = None,
    strict: bool = False,
) -> Any | None:
    """Evaluate a jsonpath expression on the target object (operand)."""

    if operand is None or not isinstance(operand, dict | list):
        logger.error("Invalid operand for jsonpath", operand=operand)
        raise TracecatExpressionError(
            f"A dict or list operand is required as jsonpath target. Got {type(operand)}"
        )
    try:
        # Try to evaluate the expression
        jsonpath_expr = jsonpath_ng.ext.parse(expr)
    except JsonPathParserError as e:
        logger.error(
            "Invalid jsonpath expression", expr=repr(expr), context_type=context_type
        )
        formatted_expr = _expr_with_context(expr, context_type)
        raise TracecatExpressionError(f"Invalid jsonpath {formatted_expr!r}") from e
    matches = [found.value for found in jsonpath_expr.find(operand)]
    if len(matches) > 1 or "[*]" in expr:
        # If there are multiple matches or array wildcard, return the list
        return matches
    elif len(matches) == 1:
        # If there is a non-array wildcard single match, return the value
        return matches[0]
    else:
        # We should only reach this point if the jsonpath didn't match
        # If there are no matches, raise an error if strict is True

        if strict:
            # We know that if this function is called, there was a templated field.
            # Therefore, it means the jsonpath was valid but there was no match.
            logger.error("Jsonpath no match", expr=repr(expr), operand=operand)
            formatted_expr = _expr_with_context(expr, context_type)
            raise TracecatExpressionError(
                f"Couldn't resolve expression {formatted_expr!r} in the context",
                detail={"expression": formatted_expr, "operand": operand},
            )
        # Return None instead of empty list
        return None
