from __future__ import annotations

import ast
import base64
import itertools
import operator
import re
from collections.abc import Callable
from datetime import datetime, timedelta
from functools import wraps
from html.parser import HTMLParser
from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network
from typing import Any, ParamSpec, TypedDict, TypeVar

import jsonpath_ng
import orjson
from jsonpath_ng.exceptions import JsonPathParserError

from tracecat.expressions.shared import ExprContext
from tracecat.expressions.validation import is_iterable
from tracecat.logging import logger
from tracecat.types.exceptions import TracecatExpressionError

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


def _bool(x: Any) -> bool:
    if isinstance(x, bool):
        return x
    if isinstance(x, str):
        return x.lower() in ("true", "1")
    # Use default bool for everything else
    return bool(x)


def from_timestamp(x: int, unit: str) -> datetime:
    if unit == "ms":
        dt = datetime.fromtimestamp(x / 1000)
    else:
        dt = datetime.fromtimestamp(x)
    return dt


def format_string(template: str, *values: Any) -> str:
    """Format a string with the given arguments."""
    return template.format(*values)


def str_to_b64(x: str) -> str:
    return base64.b64encode(x.encode()).decode()


def b64_to_str(x: str) -> str:
    return base64.b64decode(x).decode()


def ipv4_in_subnet(ipv4: str, subnet: str) -> bool:
    if IPv4Address(ipv4) in IPv4Network(subnet):
        return True
    return False


def ipv6_in_subnet(ipv6: str, subnet: str) -> bool:
    if IPv6Address(ipv6) in IPv6Network(subnet):
        return True
    return False


def ipv4_is_public(ipv4: str) -> bool:
    return IPv4Address(ipv4).is_global


def ipv6_is_public(ipv6: str) -> bool:
    return IPv6Address(ipv6).is_global


class SafeEvaluator(ast.NodeVisitor):
    SAFE_NODES = {
        ast.arguments,
        ast.Load,
        ast.arg,
        ast.Name,
        ast.Compare,
        ast.BinOp,
        ast.BoolOp,
        ast.And,
        ast.Or,
        ast.UnaryOp,
        ast.Not,
        ast.Eq,
        ast.NotEq,
        ast.Lt,
        ast.LtE,
        ast.Gt,
        ast.GtE,
        ast.Constant,
        ast.In,
        ast.List,
        ast.Call,
        ast.NotIn,
    }

    ALLOWED_FUNCTIONS = {"len"}
    ALLOWED_SYMBOLS = {"x", "len"}

    def visit(self, node):
        if type(node) not in self.SAFE_NODES:
            raise ValueError(
                f"Unsafe node {type(node).__name__} detected in expression"
            )
        if isinstance(node, ast.Call) and node.func.id not in self.ALLOWED_FUNCTIONS:
            raise ValueError("Only len() function calls are allowed in expression")
        if isinstance(node, ast.Name) and node.id not in self.ALLOWED_SYMBOLS:
            raise ValueError("Only variable x is allowed in expression")
        self.generic_visit(node)


T = TypeVar("T")


class FunctionConstraint(TypedDict):
    jsonpath: str | None
    function: str


class OperatorConstraint(TypedDict):
    jsonpath: str | None
    operator: str
    target: Any


def custom_filter(
    items: list[T], constraint: str | list[T] | FunctionConstraint | OperatorConstraint
) -> list[T]:
    logger.warning("Using custom filter function")
    match constraint:
        case str():
            return lambda_filter(collection=items, filter_expr=constraint)
        case list():
            cons = set(constraint)
            return [item for item in items if item in cons]
        case {"jsonpath": jsonpath, "operator": operator, "target": target}:
            logger.warning("Using jsonpath filter")

            def op(a, b):
                return OPERATORS[operator](a, b)

            return [item for item in items if op(eval_jsonpath(jsonpath, item), target)]

        case {"function": func_name, **kwargs}:
            logger.warning("Using function filter", func_name=func_name, kwargs=kwargs)
            # Test the function on the jsonpath of each item
            fn = FUNCTION_MAPPING[func_name]

            match kwargs:
                case {"jsonpath": jsonpath, **fn_args}:
                    return [
                        item
                        for item in items
                        if fn(eval_jsonpath(jsonpath, item), **fn_args)
                    ]
            return [item for item in items if fn(item)]
        case _:
            raise ValueError(
                f"Invalid constraint type {constraint!r} for filter operation."
            )


def lambda_filter(collection: list[T], filter_expr: str) -> list[T]:
    """Filter a collection based on a condition.

    This function references each collection item as `x` in the lambda expression.

    e.g. `x > 2` will filter out all items less than or equal to 2.
    """
    # Check if the string has any blacklisted symbols
    if any(
        word in filter_expr
        for word in ("eval", "lambda", "import", "from", "os", "sys", "exec")
    ):
        raise ValueError("Expression contains blacklisted symbols")
    expr_ast = ast.parse(filter_expr, mode="eval").body

    # Ensure the parsed AST is a comparison or logical expression
    if not isinstance(expr_ast, ast.Compare | ast.BoolOp | ast.BinOp | ast.UnaryOp):
        raise ValueError("Expression must be a Comparison")

    # Ensure the expression complies with the SafeEvaluator
    SafeEvaluator().visit(expr_ast)
    # Check the AST for safety
    lambda_expr_ast = ast.parse(f"lambda x: {filter_expr}", mode="eval").body

    # Compile the AST node into a code object
    code = compile(ast.Expression(lambda_expr_ast), "<string>", "eval")

    # Create a function from the code object
    lambda_func = eval(code)

    # Apply the lambda function to filter the collection
    filtered_collection = list(filter(lambda_func, collection))

    return filtered_collection


def cast(x: Any, typename: str) -> Any:
    if typename not in BUILTIN_TYPE_MAPPING:
        raise ValueError(f"Unknown type {typename!r} for cast operation.")
    return BUILTIN_TYPE_MAPPING[typename](x)


def _expr_with_context(expr: str, context_type: ExprContext | None) -> str:
    return f"{context_type}.{expr}" if context_type else expr


def eval_jsonpath(
    expr: str,
    operand: dict[str, Any],
    *,
    context_type: ExprContext | None = None,
    strict: bool = False,
) -> T | None:
    """Evaluate a jsonpath expression on the operand.

    Parameters
    ----------
    expr : str
        The jsonpath expression to evaluate.
    operand : dict[str, Any]
        The operand to evaluate the jsonpath on.
    context_type : ExprContext, optional
        The context type of the expression, by default None.
    strict : bool, optional
        Whether to raise an error if the jsonpath doesn't match, by default False.
    """

    if operand is None or not isinstance(operand, dict | list):
        logger.error("Invalid operand for jsonpath", operand=operand)
        raise TracecatExpressionError(
            "A dict or list operand is required as jsonpath target."
        )
    try:
        # Try to evaluate the expression
        jsonpath_expr = jsonpath_ng.parse(expr)
    except JsonPathParserError as e:
        logger.error(
            "Invalid jsonpath expression", expr=repr(expr), context_type=context_type
        )
        formatted_expr = _expr_with_context(expr, context_type)
        raise TracecatExpressionError(f"Invalid jsonpath {formatted_expr!r}") from e
    matches = [found.value for found in jsonpath_expr.find(operand)]
    if len(matches) == 1:
        return matches[0]
    elif len(matches) > 1:
        # If there are multiple matches, return the list
        return matches
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


def to_datetime(x: Any) -> datetime:
    if isinstance(x, datetime):
        return x
    if isinstance(x, int):
        return datetime.fromtimestamp(x)
    if isinstance(x, str):
        return datetime.fromisoformat(x)
    raise ValueError(f"Invalid datetime value {x!r}")


def extract_text_from_html(input: str) -> list[str]:
    parser = HTMLToTextParser()
    parser.feed(input)
    parser.close()
    return parser._output


class HTMLToTextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self._output = []
        self.convert_charrefs = True

    def handle_data(self, data):
        self._output += [data.strip()]


def custom_chain(*args):
    for arg in args:
        if is_iterable(arg, container_only=True):
            yield from custom_chain(*arg)
        else:
            yield arg


def deserialize_ndjson(x: str) -> list[dict[str, Any]]:
    return [orjson.loads(line) for line in x.splitlines()]


BUILTIN_TYPE_MAPPING = {
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
    "flatten": lambda iterables: list(custom_chain(*iterables)),
    "unique": lambda items: list(set(items)),
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
    "format": format_string,
    "filter": custom_filter,
    "jsonpath": eval_jsonpath,
    # Logical
    "and": lambda a, b: a and b,
    "or": lambda a, b: a or b,
    "not": lambda a: not a,
    # Type conversion
    # Convert JSON to string
    "serialize_json": lambda x: orjson.dumps(x).decode(),
    # Convert JSON string to dictionary
    "deserialize_json": orjson.loads,
    # Convert NDJSON to list of dictionaries
    "deserialize_ndjson": deserialize_ndjson,
    "extract_text_from_html": extract_text_from_html,
    # Time related
    "from_timestamp": lambda x, unit,: from_timestamp(x, unit),
    "now": datetime.now,
    "minutes": lambda x: timedelta(minutes=x),
    "to_datetime": to_datetime,
    # Base64
    "to_base64": str_to_b64,
    "from_base64": b64_to_str,
    # Utils
    "lookup": lambda d, k: d.get(k),
    # IP addresses
    "ipv4_in_subnet": lambda ip, subnet: ipv4_in_subnet(ip, subnet),
    "ipv6_in_subnet": lambda ip, subnet: ipv4_in_subnet(ip, subnet),
    "ipv4_is_public": ipv4_is_public,
    "ipv6_is_public": ipv6_is_public,
}


P = ParamSpec("P")
R = TypeVar("R")


def mappable(func: Callable[P, R]) -> Callable[P, R]:
    @wraps(func)
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)

    def broadcast_map(*args: Any) -> list[Any]:
        if not any(is_iterable(arg) for arg in args):
            return [func(*args)]
        # If all arguments are not iterable, return the result of the function
        iterables = (arg if is_iterable(arg) else itertools.repeat(arg) for arg in args)

        # Zip the iterables together and call the function for each set of arguments
        zipped_args = zip(*iterables, strict=False)
        return [func(*zipped) for zipped in zipped_args]

    wrapper.map = broadcast_map
    return wrapper


FUNCTION_MAPPING = {k: mappable(v) for k, v in _FUNCTION_MAPPING.items()}
