from __future__ import annotations

import ast
import base64
import ipaddress
import itertools
import json
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import datetime, timedelta
from functools import wraps
from html.parser import HTMLParser
from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network
from typing import Any, ParamSpec, TypeVar
from typing import cast as type_cast
from uuid import uuid4

import jsonpath_ng.ext
import orjson
from jsonpath_ng.exceptions import JsonPathParserError

from tracecat.expressions.shared import ExprContext
from tracecat.expressions.validation import is_iterable
from tracecat.logger import logger
from tracecat.types.exceptions import TracecatExpressionError


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

    def visit(self, node):
        if type(node) in self.RESTRICTED_NODES:
            raise ValueError(
                f"Restricted node {type(node).__name__} detected in expression"
            )
        if (
            isinstance(node, ast.Call)
            and (attr := getattr(node.func, "attr", None)) in self.RESTRICTED_SYMBOLS
        ):
            raise ValueError(f"Calling restricted functions are not allowed: {attr}")
        self.generic_visit(node)


def _build_safe_lambda(lambda_expr: str) -> Callable[[Any], Any]:
    """Build a safe lambda function from a string expression."""
    # Check if the string has any blacklisted symbols
    lambda_expr = lambda_expr.strip()
    if any(word in lambda_expr for word in SafeEvaluator.RESTRICTED_SYMBOLS):
        raise ValueError("Expression contains restricted symbols")
    expr_ast = ast.parse(lambda_expr, mode="eval").body

    # Ensure the parsed AST is a comparison or logical expression
    if not isinstance(expr_ast, ast.Lambda):
        raise ValueError("Expression must be a lambda function")

    # Ensure the expression complies with the SafeEvaluator
    SafeEvaluator().visit(expr_ast)

    # Compile the AST node into a code object
    code = compile(ast.Expression(expr_ast), "<string>", "eval")

    # Create a function from the code object
    lambda_func = eval(code)
    return type_cast(Callable[[Any], Any], lambda_func)


def _bool(x: Any) -> bool:
    """Convert input to boolean."""
    if isinstance(x, bool):
        return x
    if isinstance(x, str):
        return x.lower() in ("true", "1")
    # Use default bool for everything else
    return bool(x)


def from_timestamp(x: int, unit: str) -> datetime:
    """Convert timestamp to datetime, handling milliseconds if unit is 'ms'."""
    if unit == "ms":
        dt = datetime.fromtimestamp(x / 1000)
    else:
        dt = datetime.fromtimestamp(x)
    return dt


def format_string(template: str, *values: Any) -> str:
    """Format a string with the given arguments."""
    return template.format(*values)


def strip(x: str, chars: str) -> str:
    """Removes all leading and trailing characters."""
    return x.strip(chars)


def str_to_b64(x: str) -> str:
    """Encode string to base64."""
    return base64.b64encode(x.encode()).decode()


def b64_to_str(x: str) -> str:
    """Decode base64 string to string."""
    return base64.b64decode(x).decode()


def str_to_b64url(x: str) -> str:
    """Encode string to URL-safe base64."""
    return base64.urlsafe_b64encode(x.encode()).decode()


def b64url_to_str(x: str) -> str:
    """Decode URL-safe base64 string to string."""
    return base64.urlsafe_b64decode(x).decode()


def ipv4_in_subnet(ipv4: str, subnet: str) -> bool:
    """Check if IPv4 address is in the given subnet."""
    if IPv4Address(ipv4) in IPv4Network(subnet):
        return True
    return False


def ipv6_in_subnet(ipv6: str, subnet: str) -> bool:
    """Check if IPv6 address is in the given subnet."""
    if IPv6Address(ipv6) in IPv6Network(subnet):
        return True
    return False


def ipv4_is_public(ipv4: str) -> bool:
    """Check if IPv4 address is public/global."""
    return IPv4Address(ipv4).is_global


def ipv6_is_public(ipv6: str) -> bool:
    """Check if IPv6 address is public/global."""
    return IPv6Address(ipv6).is_global


def to_datetime(x: Any) -> datetime:
    """Convert input to datetime object from timestamp, ISO string or existing datetime."""
    if isinstance(x, datetime):
        return x
    if isinstance(x, int):
        return datetime.fromtimestamp(x)
    if isinstance(x, str):
        return datetime.fromisoformat(x)
    raise ValueError(f"Invalid datetime value {x!r}")


def extract_text_from_html(input: str) -> list[str]:
    """Extract text content from HTML string using HTMLToTextParser."""
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


def custom_chain(*args) -> Any:
    """Recursively flattens nested iterables into a single generator."""
    for arg in args:
        if is_iterable(arg, container_only=True):
            yield from custom_chain(*arg)
        else:
            yield arg


def deserialize_ndjson(x: str) -> list[dict[str, Any]]:
    """Parse newline-delimited JSON string into list of dictionaries."""
    return [orjson.loads(line) for line in x.splitlines()]


def slice_str(x: str, start_index: int, length: int) -> str:
    """Extract a substring from start_index with given length."""
    return x[start_index : start_index + length]


def not_null(x: Any) -> bool:
    """Check if value is not None."""
    return x is not None


def is_null(x: Any) -> bool:
    """Check if value is None."""
    return x is None


def regex_extract(pattern: str, text: str) -> str | None:
    """Extract first match of regex pattern from text."""
    match = re.search(pattern, text)
    if match:
        return match.group(0)
    return None


def regex_match(pattern: str, text: str) -> bool:
    """Check if text matches regex pattern."""
    return bool(re.match(pattern, text))


def regex_not_match(pattern: str, text: str) -> bool:
    """Check if text does not match regex pattern."""
    return not bool(re.match(pattern, text))


def contains(item: Any, container: Sequence[Any]) -> bool:
    """Check if item exists in container."""
    return item in container


def does_not_contain(item: Any, container: Sequence[Any]) -> bool:
    """Check if item does not exist in container."""
    return item not in container


def is_empty(x: Sequence[Any]) -> bool:
    """Check if sequence is empty."""
    return len(x) == 0


def not_empty(x: Sequence[Any]) -> bool:
    """Check if sequence is not empty."""
    return len(x) > 0


def flatten(iterables: Sequence[Sequence[Any]]) -> list[Any]:
    """Flatten nested sequences into a single list."""
    return list(custom_chain(*iterables))


def unique_items(items: Sequence[Any]) -> list[Any]:
    """Return unique items from sequence."""
    return list(set(items))


def join_strings(items: Sequence[str], sep: str) -> str:
    """Join sequence of strings with separator."""
    return sep.join(items)


def concat_strings(*items: str) -> str:
    """Concatenate multiple strings."""
    return "".join(items)


def zip_iterables(*iterables: Sequence[Any]) -> list[tuple[Any, ...]]:
    """Zip multiple sequences together."""
    return list(zip(*iterables, strict=False))


def iter_product(*iterables: Sequence[Any]) -> list[tuple[Any, ...]]:
    """Generate cartesian product of sequences."""
    return list(itertools.product(*iterables))


def generate_uuid() -> str:
    """Generate a random UUID string."""
    return str(uuid4())


def dict_keys(x: dict[Any, Any]) -> list[Any]:
    """Extract keys from dictionary."""
    return list(x.keys())


def dict_values(x: dict[Any, Any]) -> list[Any]:
    """Extract values from dictionary."""
    return list(x.values())


def serialize_to_json(x: Any) -> str:
    """Convert object to JSON string."""
    return orjson.dumps(x).decode()


def prettify_json_str(x: Any) -> str:
    """Convert object to formatted JSON string."""
    return json.dumps(x, indent=2)


def to_timestamp_str(x: datetime) -> float:
    """Convert datetime to timestamp."""
    return x.timestamp()


def create_minutes(x: int) -> timedelta:
    """Create timedelta with specified minutes."""
    return timedelta(minutes=x)


def to_date_string(x: datetime, format: str) -> str:
    """Format datetime to string using specified format."""
    return x.strftime(format)


def to_iso_format(x: datetime) -> str:
    """Convert datetime to ISO format string."""
    return x.isoformat()


def dict_lookup(d: dict[Any, Any], k: Any) -> Any:
    """Safely get value from dictionary."""
    return d.get(k)


def check_ip_version(ip: str) -> int:
    """Get IP address version (4 or 6)."""
    return ipaddress.ip_address(ip).version


def less_than(a: Any, b: Any) -> bool:
    """Check if a is less than b."""
    return a < b


def less_than_or_equal(a: Any, b: Any) -> bool:
    """Check if a is less than or equal to b."""
    return a <= b


def greater_than(a: Any, b: Any) -> bool:
    """Check if a is greater than b."""
    return a > b


def greater_than_or_equal(a: Any, b: Any) -> bool:
    """Check if a is greater than or equal to b."""
    return a >= b


def not_equal(a: Any, b: Any) -> bool:
    """Check if a is not equal to b."""
    return a != b


def is_equal(a: Any, b: Any) -> bool:
    """Check if a is equal to b."""
    return a == b


def add(a: float | int, b: float | int) -> float | int:
    """Add two numbers together."""
    return a + b


def sub(a: float | int, b: float | int) -> float | int:
    """Subtract second number from first number."""
    return a - b


def mul(a: float | int, b: float | int) -> float | int:
    """Multiply two numbers together."""
    return a * b


def div(a: float | int, b: float | int) -> float:
    """Divide first number by second number."""
    if b == 0:
        raise ZeroDivisionError("Cannot divide by zero")
    return a / b


def mod(a: float | int, b: float | int) -> float | int:
    """Calculate modulo (remainder) of first number divided by second."""
    if b == 0:
        raise ZeroDivisionError("Cannot calculate modulo with zero")
    return a % b


def pow(a: float | int, b: float | int) -> float | int:
    """Raise first number to the power of second number."""
    return a**b


def now() -> datetime:
    """Return the current datetime."""
    return datetime.now()


def sum_(iterable: Iterable[float | int], start: float | int = 0) -> float | int:
    """Return the sum of a 'start' value (default: 0) plus an iterable of numbers."""
    return sum(iterable, start)


def not_(x: bool) -> bool:
    """Logical NOT operation."""
    return not x


def and_(a: bool, b: bool) -> bool:
    """Logical AND operation."""
    return a and b


def or_(a: bool, b: bool) -> bool:
    """Logical OR operation."""
    return a or b


def intersect[T: Any](
    items: Sequence[T], collection: Sequence[T], jsonpath: str | None = None
) -> list[T]:
    """Return the set intersection of two sequences as a list."""
    col_set = set(collection)
    if jsonpath:
        return list(
            {item for item in items if eval_jsonpath(jsonpath, item) in col_set}
        )
    return list({item for item in items if item in collection})


def union[T: Any](*collections: Sequence[T]) -> list[T]:
    """Return the set union of multiple sequences as a list."""
    return list(set().union(*collections))


def apply[T: Any](item: T | Iterable[T], python_lambda: str) -> T | list[T]:
    """Apply a Python lambda function to an item or sequence of items."""
    fn = _build_safe_lambda(python_lambda)
    if is_iterable(item, container_only=True):
        return [fn(i) for i in item]
    return fn(item)


def filter_[T: Any](items: Sequence[T], python_lambda: str) -> list[T]:
    """Filter a collection using a Python lambda expression."""
    fn = _build_safe_lambda(python_lambda)
    return list(filter(fn, items))


def eval_jsonpath(
    expr: str,
    operand: Mapping[str, Any],
    *,
    context_type: ExprContext | None = None,
    strict: bool = False,
) -> Any | None:
    """Evaluate a jsonpath expression on the target object (operand)."""

    if operand is None or not isinstance(operand, dict | list):
        logger.error("Invalid operand for jsonpath", operand=operand)
        raise TracecatExpressionError(
            "A dict or list operand is required as jsonpath target."
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


_FUNCTION_MAPPING = {
    # String transforms
    "slice": slice_str,
    "strip": strip,
    # Comparison
    "less_than": less_than,
    "less_than_or_equal": less_than_or_equal,
    "greater_than": greater_than,
    "greater_than_or_equal": greater_than_or_equal,
    "not_equal": not_equal,
    "is_equal": is_equal,
    "not_null": not_null,
    "is_null": is_null,
    # Regex
    "regex_extract": regex_extract,
    "regex_match": regex_match,
    "regex_not_match": regex_not_match,
    # Collections
    "contains": contains,
    "does_not_contain": does_not_contain,
    "length": len,
    "is_empty": is_empty,
    "not_empty": not_empty,
    "flatten": flatten,
    "unique": unique_items,
    # Set operations
    "intersect": intersect,
    "union": union,
    # Math
    "add": add,
    "sub": sub,
    "mul": mul,
    "div": div,
    "mod": mod,
    "pow": pow,
    "sum": sum_,
    # Transform
    "join": join_strings,
    "concat": concat_strings,
    "format": format_string,
    "apply": apply,
    "filter": filter_,
    # Iteration
    "zip": zip_iterables,
    "iter_product": iter_product,
    # Generators
    "uuid4": generate_uuid,
    # Extract JSON keys and values
    "to_keys": dict_keys,
    "to_values": dict_values,
    # Logical
    "and": and_,
    "or": or_,
    "not": not_,
    # Type conversion
    "serialize_json": serialize_to_json,
    "deserialize_json": orjson.loads,
    "prettify_json": prettify_json_str,
    "deserialize_ndjson": deserialize_ndjson,
    "extract_text_from_html": extract_text_from_html,
    # Time related
    "from_timestamp": from_timestamp,
    "to_timestamp": to_timestamp_str,
    "minutes": create_minutes,
    "now": now,
    "to_datestring": to_date_string,
    "to_datetime": to_datetime,
    "to_isoformat": to_iso_format,
    # Base64
    "to_base64": str_to_b64,
    "from_base64": b64_to_str,
    "to_base64url": str_to_b64url,
    "from_base64url": b64url_to_str,
    # Utils
    "lookup": dict_lookup,
    # IP addresses
    "ipv4_in_subnet": ipv4_in_subnet,
    "ipv6_in_subnet": ipv6_in_subnet,
    "ipv4_is_public": ipv4_is_public,
    "ipv6_is_public": ipv6_is_public,
    "check_ip_version": check_ip_version,
}

OPERATORS = {
    "||": or_,
    "&&": and_,
    "==": is_equal,
    "!=": not_equal,
    "<": less_than,
    "<=": less_than_or_equal,
    ">": greater_than,
    ">=": greater_than_or_equal,
    "+": add,
    "-": sub,
    "*": mul,
    "/": div,
    "%": mod,
    "!": not_,
}


P = ParamSpec("P")
R = TypeVar("R")
F = Callable[P, R]


def mappable(func: F) -> F:
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
    wrapper.__doc__ = func.__doc__
    return wrapper


FUNCTION_MAPPING = {k: mappable(v) for k, v in _FUNCTION_MAPPING.items()}
"""Mapping of function names to decorated mappable versions."""

BUILTIN_TYPE_MAPPING = {
    "int": int,
    "float": float,
    "str": str,
    "bool": _bool,
    "datetime": to_datetime,
    # TODO: Perhaps support for URLs for files?
}
"""Built-in type mapping for cast operations."""


# Utility functions


def cast(x: Any, typename: str) -> Any:
    if typename not in BUILTIN_TYPE_MAPPING:
        raise ValueError(f"Unknown type {typename!r} for cast operation.")
    return BUILTIN_TYPE_MAPPING[typename](x)


def _expr_with_context(expr: str, context_type: ExprContext | None) -> str:
    return f"{context_type}.{expr}" if context_type else expr
