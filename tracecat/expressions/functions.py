from __future__ import annotations

import ast
import base64
import ipaddress
import itertools
import json
import math
import re
import zoneinfo
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from functools import wraps
from html.parser import HTMLParser
from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network
from typing import Any, Literal, ParamSpec, TypeVar
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


# String functions


def from_timestamp(x: int, unit: str) -> datetime:
    """Convert timestamp to datetime, handling milliseconds if unit is 'ms'."""
    if unit == "ms":
        dt = datetime.fromtimestamp(x / 1000)
    else:
        dt = datetime.fromtimestamp(x)
    return dt


def to_datetime(x: Any, timezone: str | None = None) -> datetime:
    """Convert input to datetime object from timestamp, ISO string or existing datetime.
    Supports timezone-aware datetime objects if IANA timezone is provided.
    """
    tzinfo = None
    if timezone:
        tzinfo = zoneinfo.ZoneInfo(timezone)

    if isinstance(x, datetime):
        dt = x
    elif isinstance(x, int):
        dt = datetime.fromtimestamp(x)
    elif isinstance(x, str):
        dt = datetime.fromisoformat(x)
    else:
        raise ValueError(f"Invalid datetime value {x!r}")

    if tzinfo:
        dt = dt.astimezone(tzinfo)

    return dt


def add_prefix(x: str | list[str], prefix: str) -> str | list[str]:
    """Add a prefix to a string or list of strings."""
    if is_iterable(x, container_only=True):
        return [f"{prefix}{item}" for item in x]
    return f"{prefix}{x}"


def add_suffix(x: str | list[str], suffix: str) -> str | list[str]:
    """Add a suffix to a string or list of strings."""
    if is_iterable(x, container_only=True):
        return [f"{item}{suffix}" for item in x]
    return f"{x}{suffix}"


def format_string(template: str, *values: Any) -> str:
    """Format a string with the given arguments."""
    return template.format(*values)


def capitalize(x: str) -> str:
    """Capitalize a string."""
    return x.capitalize()


def titleize(x: str) -> str:
    """Convert a string to titlecase."""
    return x.title()


def uppercase(x: str) -> str:
    """Convert string to uppercase."""
    return x.upper()


def lowercase(x: str) -> str:
    """Convert string to lowercase."""
    return x.lower()


def slice_str(x: str, start_index: int, length: int) -> str:
    """Extract a substring from start_index with given length."""
    return x[start_index : start_index + length]


def endswith(x: str, suffix: str) -> bool:
    """Check if a string ends with a specified suffix."""
    return x.endswith(suffix)


def startswith(x: str, suffix: str) -> bool:
    """Check if a string starts wit a specified suffix."""
    return x.startswith(suffix)


def split(x: str, sep: str) -> list[str]:
    """Split a string into a list of strings by a seperator."""
    return x.split(sep)


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


def generate_uuid() -> str:
    """Generate a random UUID string."""
    return str(uuid4())


def deserialize_ndjson(x: str) -> list[dict[str, Any]]:
    """Parse newline-delimited JSON string into list of dictionaries."""
    return [orjson.loads(line) for line in x.splitlines()]


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


# IP address functions


def check_ip_version(ip: str) -> int:
    """Get IP address version (4 or 6)."""
    return ipaddress.ip_address(ip).version


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


# Numeric functions


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


def sum_(iterable: Iterable[float | int], start: float | int = 0) -> float | int:
    """Return the sum of a 'start' value (default: 0) plus an iterable of numbers."""
    return sum(iterable, start)


def round_up(x: float) -> int:
    """Round up to the closest integer."""
    return math.ceil(x)


def round_down(x: float) -> int:
    """Round down to the closest integer"""
    return math.floor(x)


# Array functions


def custom_chain(*args) -> Any:
    """Recursively flattens nested iterables into a single generator."""
    for arg in args:
        if is_iterable(arg, container_only=True):
            yield from custom_chain(*arg)
        else:
            yield arg


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


def create_range(start: int, end: int, step: int = 1) -> range:
    """Create a range of integers from start to end (exclusive), with a step size."""
    return range(start, end, step)


# Dictionary functions


def dict_keys(x: dict[Any, Any]) -> list[Any]:
    """Extract keys from dictionary."""
    return list(x.keys())


def dict_lookup(d: dict[Any, Any], k: Any) -> Any:
    """Safely get value from dictionary."""
    return d.get(k)


def dict_values(x: dict[Any, Any]) -> list[Any]:
    """Extract values from dictionary."""
    return list(x.values())


def serialize_to_json(x: Any) -> str:
    """Convert object to JSON string."""
    return orjson.dumps(x).decode()


def prettify_json_str(x: Any) -> str:
    """Convert object to formatted JSON string."""
    return json.dumps(x, indent=2)


# Time-related functions


def to_timestamp_str(x: datetime) -> float:
    """Convert datetime to timestamp."""
    return x.timestamp()


def create_datetime(
    year: int,
    month: int,
    day: int,
    hour: int = 0,
    minute: int = 0,
    second: int = 0,
) -> datetime:
    """Create datetime from year, month, day, hour, minute, and second."""
    return datetime(year, month, day, hour, minute, second)


def get_second(x: datetime) -> int:
    """Get second (0-59) from datetime."""
    return x.second


def get_minute(x: datetime) -> int:
    """Get minute (0-59) from datetime."""
    return x.minute


def get_hour(x: datetime) -> int:
    """Get hour (0-23) from datetime."""
    return x.hour


def get_day_of_week(
    x: datetime, format: Literal["number", "full", "short"] = "number"
) -> int | str:
    """Extract day of week from datetime. Returns 0-6 (Mon-Sun) or day name if format is "full" or "short"."""
    weekday = x.weekday()
    match format:
        case "number":
            return weekday
        case "full":
            days = [
                "Monday",
                "Tuesday",
                "Wednesday",
                "Thursday",
                "Friday",
                "Saturday",
                "Sunday",
            ]
            return days[weekday]
        case "short":
            days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
            return days[weekday]
        case _:
            raise ValueError("format must be 'number', 'full', or 'short'")


def get_day(x: datetime) -> int:
    """Get day of month (1-31) from datetime."""
    return x.day


def get_month(
    x: datetime, format: Literal["number", "full", "short"] = "number"
) -> int | str:
    """Extract month from datetime. Returns 1-12 or month name if format is "full" or "short"."""
    month = x.month
    match format:
        case "number":
            return month
        case "full":
            months = [
                "January",
                "February",
                "March",
                "April",
                "May",
                "June",
                "July",
                "August",
                "September",
                "October",
                "November",
                "December",
            ]
            return months[month - 1]
        case "short":
            months = [
                "Jan",
                "Feb",
                "Mar",
                "Apr",
                "May",
                "Jun",
                "Jul",
                "Aug",
                "Sep",
                "Oct",
                "Nov",
                "Dec",
            ]
            return months[month - 1]
        case _:
            raise ValueError("format must be 'number', 'full', or 'short'")


def get_year(x: datetime) -> int:
    """Get year from datetime."""
    return x.year


def create_seconds(x: int) -> timedelta:
    """Create timedelta with specified seconds."""
    return timedelta(seconds=x)


def create_minutes(x: int) -> timedelta:
    """Create timedelta with specified minutes."""
    return timedelta(minutes=x)


def create_hours(x: int) -> timedelta:
    """Create timedelta with specified hours."""
    return timedelta(hours=x)


def create_days(x: int) -> timedelta:
    """Create timedelta with specified days."""
    return timedelta(days=x)


def create_weeks(x: int) -> timedelta:
    """Create timedelta with spcified weeks"""
    return timedelta(weeks=x)


def seconds_between(start: datetime, end: datetime) -> float:
    """Seconds between two datetimes."""
    return (end - start).total_seconds()


def minutes_between(start: datetime, end: datetime) -> float:
    """Minutes between two datetimes."""
    return (end - start).total_seconds() / 60


def hours_between(start: datetime, end: datetime) -> float:
    """Hours between two datetimes."""
    return (end - start).total_seconds() / 3600


def days_between(start: datetime, end: datetime) -> float:
    """Days between two datetimes."""
    return (end - start).total_seconds() / 86400


def weeks_between(start: datetime, end: datetime) -> float:
    """Weeks between two datetimes or dates."""
    return (end - start).total_seconds() / 604800


def to_date_string(x: datetime, format: str) -> str:
    """Format datetime to string using specified format."""
    return x.strftime(format)


def to_iso_format(x: datetime) -> str:
    """Convert datetime to ISO format string."""
    return x.isoformat()


def now() -> datetime:
    """Return the current datetime."""
    return datetime.now()


def utcnow() -> datetime:
    """Return the current timezone-aware datetime."""
    return datetime.now(UTC)


def today() -> date:
    """Return the current date."""
    return date.today()


def set_timezone(x: datetime, timezone: str) -> datetime:
    """Convert datetime to different timezone. Timezone must be a valid IANA timezone name (e.g., "America/New_York")."""
    tz = zoneinfo.ZoneInfo(timezone)
    return x.astimezone(tz)


def unset_timezone(x: datetime) -> datetime:
    """Remove timezone information from datetime without changing the time."""
    return x.replace(tzinfo=None)


def windows_filetime(x: datetime) -> int:
    """Convert datetime to Windows filetime."""
    # Define Windows and Unix epochs
    windows_epoch = datetime(1601, 1, 1, tzinfo=UTC)

    # Ensure input datetime is UTC
    if x.tzinfo is None:
        x = x.replace(tzinfo=UTC)
    elif x.tzinfo != UTC:
        x = x.astimezone(UTC)

    # Calculate number of 100-nanosecond intervals since Windows epoch
    delta = x - windows_epoch
    return int(delta.total_seconds() * 10_000_000)


# Comparison functions


def not_null(x: Any) -> bool:
    """Check if value is not None."""
    return x is not None


def is_null(x: Any) -> bool:
    """Check if value is None."""
    return x is None


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


def not_(x: bool) -> bool:
    """Logical NOT operation."""
    return not x


def and_(a: bool, b: bool) -> bool:
    """Logical AND operation."""
    return a and b


def or_(a: bool, b: bool) -> bool:
    """Logical OR operation."""
    return a or b


# Filtering functions


def intersect[T: Any](
    items: Sequence[T], collection: Sequence[T], python_lambda: str | None = None
) -> list[T]:
    """Return the set intersection of two sequences as a list. If a Python lambda is provided, it will be applied to each item before checking for intersection."""
    col_set = set(collection)
    if python_lambda:
        fn = _build_safe_lambda(python_lambda)
        result = {item for item in items if fn(item) in col_set}
    else:
        result = set(items) & col_set
    return list(result)


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
    """Filter a collection using a Python lambda expression as a string (e.g. `"lambda x: x > 2"`)."""
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
    "prefix": add_prefix,
    "suffix": add_suffix,
    "capitalize": capitalize,
    "endswith": endswith,
    "lowercase": lowercase,
    "slice": slice_str,
    "split": split,
    "startswith": startswith,
    "strip": strip,
    "titleize": titleize,
    "uppercase": uppercase,
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
    "range": create_range,
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
    "get_second": get_second,
    "get_minute": get_minute,
    "get_hour": get_hour,
    "get_day": get_day,
    "get_day_of_week": get_day_of_week,
    "get_month": get_month,
    "get_year": get_year,
    "datetime": create_datetime,
    "seconds": create_seconds,
    "minutes": create_minutes,
    "hours": create_hours,
    "days": create_days,
    "weeks": create_weeks,
    "seconds_between": seconds_between,
    "minutes_between": minutes_between,
    "hours_between": hours_between,
    "days_between": days_between,
    "weeks_between": weeks_between,
    "now": now,
    "utcnow": utcnow,
    "today": today,
    "set_timezone": set_timezone,
    "unset_timezone": unset_timezone,
    "to_datestring": to_date_string,
    "to_datetime": to_datetime,
    "to_isoformat": to_iso_format,
    "to_timestamp": to_timestamp_str,
    "windows_filetime": windows_filetime,
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
