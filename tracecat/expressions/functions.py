import base64
import itertools
import operator
import re
from collections.abc import Callable
from datetime import datetime
from functools import wraps
from typing import Any, ParamSpec, TypeVar

import jsonpath_ng
import orjson
from jsonpath_ng.exceptions import JsonPathParserError

from tracecat.expressions.shared import ExprContext
from tracecat.expressions.validation import is_iterable
from tracecat.logging import logger
from tracecat.types.exceptions import TracecatExpressionError


def _bool(x: Any) -> bool:
    if isinstance(x, bool):
        return x
    if isinstance(x, str):
        return x.lower() in ("true", "1")
    # Use default bool for everything else
    return bool(x)


def _from_timestamp(x: int, unit: str) -> datetime:
    if unit == "ms":
        dt = datetime.fromtimestamp(x / 1000)
    else:
        dt = datetime.fromtimestamp(x)
    return dt


def _format_string(template: str, *values: Any) -> str:
    """Format a string with the given arguments."""
    return template.format(*values)


def _str_to_b64(x: str) -> str:
    return base64.b64encode(x.encode()).decode()


def _b64_to_str(x: str) -> str:
    return base64.b64decode(x).decode()


BUILTIN_TYPE_NAPPING = {
    "int": int,
    "float": float,
    "str": str,
    "bool": _bool,
    # TODO: Perhaps support for URLs for files?
}

# Supported Formulas / Functions
_FUNCTION_MAPPING = {
    # Comparison
    "less_than": operator.lt,
    "less_than_or_equal": operator.le,
    "greater_than": operator.gt,
    "greater_than_or_equal": operator.ge,
    "not_equal": operator.ne,
    "is_equal": operator.eq,
    "not_null": lambda x: x is not None,
    "is_null": lambda x: x is None,
    # Regex
    "regex_extract": lambda pattern, text: re.search(pattern, text).group(0),
    "regex_match": lambda pattern, text: bool(re.match(pattern, text)),
    "regex_not_match": lambda pattern, text: not bool(re.match(pattern, text)),
    # Collections
    "contains": lambda item, container: item in container,
    "does_not_contain": lambda item, container: item not in container,
    "length": len,
    "is_empty": lambda x: len(x) == 0,
    "not_empty": lambda x: len(x) > 0,
    # Math
    "add": operator.add,
    "sub": operator.sub,
    "mul": operator.mul,
    "div": operator.truediv,
    "mod": operator.mod,
    "pow": operator.pow,
    "sum": sum,
    # Transform
    "join": lambda items, sep: sep.join(items),
    "concat": lambda *items: "".join(items),
    "format": _format_string,
    # Logical
    "and": lambda a, b: a and b,
    "or": lambda a, b: a or b,
    "not": lambda a: not a,
    # Type conversion
    # Convert JSON to string
    "serialize_json": lambda x: orjson.dumps(x).decode(),
    "deserialize_json": lambda x: orjson.loads(x),
    # Convert timestamp to datetime
    "from_timestamp": lambda x, unit,: _from_timestamp(x, unit),
    # Base64
    "to_base64": _str_to_b64,
    "from_base64": _b64_to_str,
}

OPERATORS = {
    "||": lambda x, y: x or y,
    "&&": lambda x, y: x and y,
    "==": lambda x, y: x == y,
    "!=": lambda x, y: x != y,
    "<": lambda x, y: x < y,
    "<=": lambda x, y: x <= y,
    ">": lambda x, y: x > y,
    ">=": lambda x, y: x >= y,
    "+": lambda x, y: x + y,
    "-": lambda x, y: x - y,
    "*": lambda x, y: x * y,
    "/": lambda x, y: x / y,
    "%": lambda x, y: x % y,
    "!": lambda x: not x,
}

P = ParamSpec("P")
R = TypeVar("R")


def mappable(func: Callable[P, R]) -> Callable[P, R]:
    @wraps(func)
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)

    def broadcast_map(*args: Any) -> list[Any]:
        iterables = (arg if is_iterable(arg) else itertools.repeat(arg) for arg in args)

        # Zip the iterables together and call the function for each set of arguments
        zipped_args = zip(*iterables, strict=False)
        return [func(*zipped) for zipped in zipped_args]

    wrapper.map = broadcast_map
    return wrapper


FUNCTION_MAPPING = {k: mappable(v) for k, v in _FUNCTION_MAPPING.items()}


def cast(x: Any, typename: str) -> Any:
    if typename not in BUILTIN_TYPE_NAPPING:
        raise ValueError(f"Unknown type {typename!r} for cast operation.")
    return BUILTIN_TYPE_NAPPING[typename](x)


def _expr_with_context(expr: str, context_type: ExprContext | None) -> str:
    return f"{context_type}.{expr}" if context_type else expr


def eval_jsonpath(
    expr: str, operand: dict[str, Any], *, context_type: ExprContext | None = None
) -> Any:
    if operand is None or not isinstance(operand, dict | list):
        logger.error("Invalid operand for jsonpath", operand=operand)
        raise TracecatExpressionError(
            "A dict or list operand is required as jsonpath target."
        )
    try:
        # Try to evaluate the expression
        jsonpath_expr = jsonpath_ng.parse(expr)
    except JsonPathParserError as e:
        logger.errro(
            "Invalid jsonpath expression", expr=repr(expr), context_type=context_type
        )
        formatted_expr = _expr_with_context(expr, context_type)
        raise TracecatExpressionError(f"Invalid jsonpath {formatted_expr!r}") from e
    matches = [found.value for found in jsonpath_expr.find(operand)]
    if len(matches) == 1:
        return matches[0]
    elif len(matches) > 1:
        return matches
    else:
        # We know that if this function is called, there was a templated field.
        # Therefore, it means the jsonpath was valid but there was no match.
        logger.error("Jsonpath no match", expr=repr(expr), operand=operand)
        formatted_expr = _expr_with_context(expr, context_type)
        raise TracecatExpressionError(
            f"Couldn't resolve expression {formatted_expr!r} in the given context: {operand}.",
            detail="No match found.",
        )
