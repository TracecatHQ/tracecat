from __future__ import annotations

import base64
import ipaddress
import itertools
import json
import math
import re
import urllib.parse
import zoneinfo
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, date, datetime, timedelta
from functools import wraps
from html.parser import HTMLParser
from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network
from typing import Any, Literal, ParamSpec, TypeVar
from uuid import uuid4

import orjson
from slugify import slugify

from tracecat.expressions.validation import is_iterable


def _bool(x: Any) -> bool:
    """Convert input to boolean."""
    if isinstance(x, bool):
        return x
    if isinstance(x, str):
        return x.lower() in ("true", "1")
    # Use default bool for everything else
    return bool(x)


# String functions


def slugify_(x: str) -> str:
    """Slugify a string."""
    return slugify(x)


def url_encode(x: str) -> str:
    """Converts URL-unsafe characters into percent-encoded characters."""
    return urllib.parse.quote(x)


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


def replace(x: str, old: str, new: str) -> str:
    """Replace all occurrences of old substring with new substring."""
    return x.replace(old, new)


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


def compact(x: list[Any]) -> list[Any]:
    """Drop null values from a list. Similar to compact function in Terraform."""
    return [item for item in x if item is not None]


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


def _custom_chain(*args) -> Any:
    """Recursively flattens nested iterables into a single generator."""
    for arg in args:
        if is_iterable(arg, container_only=True):
            yield from _custom_chain(*arg)
        else:
            yield arg


def flatten(iterables: Sequence[Sequence[Any]]) -> list[Any]:
    """Flatten nested sequences into a single list."""
    return list(_custom_chain(*iterables))


def unique(items: Sequence[Any]) -> list[Any]:
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


def merge_dicts(x: dict[Any, Any], y: dict[Any, Any]) -> dict[Any, Any]:
    """Merge two dictionaries. Similar to merge function in Terraform."""
    return {**x, **y}


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


def prettify_json(x: Any) -> str:
    """Convert object to formatted JSON string."""
    return json.dumps(x, indent=2)


# Time-related functions


def to_datetime(x: Any, timezone: str | None = None) -> datetime:
    """Convert to timezone-aware datetime object from timestamp (in seconds), ISO 8601 string or existing datetime.

    Supports timezone-aware datetime objects if IANA timezone is provided.
    """
    tzinfo = None
    if timezone:
        tzinfo = zoneinfo.ZoneInfo(timezone)

    if isinstance(x, datetime):
        dt = x
    elif isinstance(x, int):
        dt = datetime.fromtimestamp(x, UTC)
    elif isinstance(x, str):
        # First try to parse with timezone info
        try:
            dt = datetime.fromisoformat(x)
        except ValueError:
            # If it fails, assume UTC for the input
            dt = datetime.fromisoformat(x).replace(tzinfo=UTC)
    else:
        raise ValueError(
            "Expected ISO 8601 string or integer timestamp in seconds. Got "
            f"{type(x)}: {x!r}"
        )

    # If input has no timezone and one is specified, assume UTC
    if dt.tzinfo is None and tzinfo:
        dt = dt.replace(tzinfo=UTC)

    # Convert to target timezone if specified
    if tzinfo:
        dt = dt.astimezone(tzinfo)

    return dt


def parse_datetime(x: str, format: str) -> datetime:
    """Parse string to datetime using specified format."""
    return datetime.strptime(x, format)


def format_datetime(x: datetime | str, format: str) -> str:
    """Format datetime into specified format (e.g. '%Y-%m-%d %H:%M:%S')."""
    if isinstance(x, str):
        x = to_datetime(x)
    return x.strftime(format)


def to_timestamp(x: datetime | str, unit: str = "s") -> int:
    """Convert datetime to timestamp in milliseconds ('ms') or seconds ('s')."""
    if isinstance(x, str):
        x = to_datetime(x)
    # If datetime has no timezone, assume UTC
    if x.tzinfo is None:
        x = x.replace(tzinfo=UTC)
    ts = x.timestamp()
    if unit == "ms":
        return int(ts * 1000)
    return int(ts)


def from_timestamp(x: int, unit: str = "s") -> datetime:
    """Convert integer timestamp in milliseconds ('ms') or seconds ('s') to datetime."""
    if unit == "ms":
        dt = datetime.fromtimestamp(x / 1000, UTC)
    else:
        dt = datetime.fromtimestamp(x, UTC)
    return dt


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


def get_second(x: datetime | str) -> int:
    """Get second (0-59) from datetime."""
    if isinstance(x, str):
        x = to_datetime(x)
    return x.second


def get_minute(x: datetime | str) -> int:
    """Get minute (0-59) from datetime."""
    if isinstance(x, str):
        x = to_datetime(x)
    return x.minute


def get_hour(x: datetime | str) -> int:
    """Get hour (0-23) from datetime."""
    if isinstance(x, str):
        x = to_datetime(x)
    return x.hour


def get_day_of_week(
    x: datetime | str, format: Literal["number", "full", "short"] = "number"
) -> int | str:
    """Extract day of week from datetime. Returns 0-6 (Mon-Sun) or day name if format is "full" or "short"."""
    if isinstance(x, str):
        x = to_datetime(x)
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


def get_day(x: datetime | str) -> int:
    """Get day of month (1-31) from datetime."""
    if isinstance(x, str):
        x = to_datetime(x)
    return x.day


def get_month(
    x: datetime | str, format: Literal["number", "full", "short"] = "number"
) -> int | str:
    """Extract month from datetime. Returns 1-12 or month name if format is "full" or "short"."""
    if isinstance(x, str):
        x = to_datetime(x)
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


def get_year(x: datetime | str) -> int:
    """Get year from datetime."""
    if isinstance(x, str):
        x = to_datetime(x)
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


def seconds_between(start: datetime | str, end: datetime | str) -> float:
    """Seconds between two datetimes."""
    if isinstance(start, str):
        start = to_datetime(start)
    if isinstance(end, str):
        end = to_datetime(end)
    return (end - start).total_seconds()


def minutes_between(start: datetime | str, end: datetime | str) -> float:
    """Minutes between two datetimes."""
    if isinstance(start, str):
        start = to_datetime(start)
    if isinstance(end, str):
        end = to_datetime(end)
    return (end - start).total_seconds() / 60


def hours_between(start: datetime | str, end: datetime | str) -> float:
    """Hours between two datetimes."""
    if isinstance(start, str):
        start = to_datetime(start)
    if isinstance(end, str):
        end = to_datetime(end)
    return (end - start).total_seconds() / 3600


def days_between(start: datetime | str, end: datetime | str) -> float:
    """Days between two datetimes."""
    if isinstance(start, str):
        start = to_datetime(start)
    if isinstance(end, str):
        end = to_datetime(end)
    return (end - start).total_seconds() / 86400


def weeks_between(start: datetime | str, end: datetime | str) -> float:
    """Weeks between two datetimes or dates."""
    if isinstance(start, str):
        start = to_datetime(start)
    if isinstance(end, str):
        end = to_datetime(end)
    return (end - start).total_seconds() / 604800


def to_isoformat(x: datetime | str) -> str:
    """Convert datetime to ISO format string."""
    if isinstance(x, str):
        x = to_datetime(x)
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


def set_timezone(x: datetime | str, timezone: str) -> datetime:
    """Convert datetime to different timezone. Timezone must be a valid IANA timezone name (e.g., "America/New_York")."""
    if isinstance(x, str):
        x = to_datetime(x)
    tz = zoneinfo.ZoneInfo(timezone)
    return x.astimezone(tz)


def unset_timezone(x: datetime | str) -> datetime:
    """Remove timezone information from datetime without changing the time."""
    if isinstance(x, str):
        x = to_datetime(x)
    return x.replace(tzinfo=None)


def windows_filetime(x: datetime | str) -> int:
    """Convert datetime to Windows filetime."""
    if isinstance(x, str):
        x = to_datetime(x)
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


_FUNCTION_MAPPING = {
    # String transforms
    "capitalize": capitalize,
    "concat": concat_strings,
    "endswith": endswith,
    "format": format_string,
    "join": join_strings,
    "lowercase": lowercase,
    "prefix": add_prefix,
    "replace": replace,
    "slice": slice_str,
    "slugify": slugify_,
    "split": split,
    "startswith": startswith,
    "strip": strip,
    "suffix": add_suffix,
    "titleize": titleize,
    "uppercase": uppercase,
    "url_encode": url_encode,
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
    # Arrays
    "compact": compact,
    "contains": contains,
    "does_not_contain": does_not_contain,
    "flatten": flatten,
    "is_empty": is_empty,
    "length": len,
    "not_empty": not_empty,
    "unique": unique,
    # Math
    "add": add,
    "sub": sub,
    "mul": mul,
    "div": div,
    "mod": mod,
    "pow": pow,
    "sum": sum_,
    # Iteration
    "zip": zip_iterables,
    "iter_product": iter_product,
    "range": create_range,
    # Generators
    "uuid4": generate_uuid,
    # JSON functions
    "lookup": dict_lookup,
    "merge": merge_dicts,
    "to_keys": dict_keys,
    "to_values": dict_values,
    # Logical
    "and": and_,
    "or": or_,
    "not": not_,
    # Type conversion
    "serialize_json": serialize_to_json,
    "deserialize_json": orjson.loads,
    "prettify_json": prettify_json,
    "deserialize_ndjson": deserialize_ndjson,
    "extract_text_from_html": extract_text_from_html,
    # Time related
    "datetime": create_datetime,
    "days_between": days_between,
    "days": create_days,
    "format_datetime": format_datetime,
    "from_timestamp": from_timestamp,
    "get_day_of_week": get_day_of_week,
    "get_day": get_day,
    "get_hour": get_hour,
    "get_minute": get_minute,
    "get_month": get_month,
    "get_second": get_second,
    "get_year": get_year,
    "hours_between": hours_between,
    "hours": create_hours,
    "minutes_between": minutes_between,
    "minutes": create_minutes,
    "now": now,
    "parse_datetime": parse_datetime,
    "seconds_between": seconds_between,
    "seconds": create_seconds,
    "set_timezone": set_timezone,
    "to_datetime": to_datetime,
    "to_isoformat": to_isoformat,
    "to_timestamp": to_timestamp,
    "today": today,
    "unset_timezone": unset_timezone,
    "utcnow": utcnow,
    "weeks_between": weeks_between,
    "weeks": create_weeks,
    "windows_filetime": windows_filetime,
    # Base64
    "to_base64": str_to_b64,
    "from_base64": b64_to_str,
    "to_base64url": str_to_b64url,
    "from_base64url": b64url_to_str,
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

    wrapper.map = broadcast_map  # type: ignore
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


def cast(x: Any, typename: str) -> Any:
    if typename not in BUILTIN_TYPE_MAPPING:
        raise ValueError(f"Unknown type {typename!r} for cast operation.")
    return BUILTIN_TYPE_MAPPING[typename](x)
