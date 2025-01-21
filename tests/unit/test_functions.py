from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import orjson
import pytest

from tracecat.expressions.functions import (
    _bool,
    _build_safe_lambda,
    add,
    add_prefix,
    add_suffix,
    and_,
    b64_to_str,
    b64url_to_str,
    capitalize,
    cast,
    check_ip_version,
    contains,
    create_days,
    create_hours,
    create_minutes,
    create_range,
    create_seconds,
    create_weeks,
    days_between,
    deserialize_ndjson,
    dict_keys,
    dict_lookup,
    dict_values,
    difference,
    div,
    does_not_contain,
    endswith,
    extract_text_from_html,
    filter_,
    flatten,
    format_datetime,
    format_string,
    from_timestamp,
    generate_uuid,
    get_day,
    get_day_of_week,
    get_hour,
    get_minute,
    get_month,
    get_second,
    get_year,
    greater_than,
    greater_than_or_equal,
    hours_between,
    intersect,
    ipv4_in_subnet,
    ipv4_is_public,
    ipv6_in_subnet,
    ipv6_is_public,
    is_empty,
    is_equal,
    is_null,
    iter_product,
    less_than,
    less_than_or_equal,
    lowercase,
    mappable,
    minutes_between,
    mod,
    mul,
    not_,
    not_empty,
    not_equal,
    not_null,
    or_,
    parse_datetime,
    pow,
    prettify_json_str,
    regex_extract,
    regex_match,
    regex_not_match,
    seconds_between,
    serialize_to_json,
    set_timezone,
    slice_str,
    split,
    startswith,
    str_to_b64,
    str_to_b64url,
    strip,
    sub,
    sum_,
    titleize,
    to_datetime,
    to_timestamp,
    union,
    unset_timezone,
    uppercase,
    url_encode,
    weeks_between,
    zip_iterables,
)


@pytest.mark.parametrize(
    "input,prefix,expected",
    [
        ("test", "prefix", "prefixtest"),
        (["hello", "world"], "prefix", ["prefixhello", "prefixworld"]),
    ],
)
def test_add_prefix(
    input: str | list[str], prefix: str, expected: str | list[str]
) -> None:
    assert add_prefix(input, prefix) == expected


@pytest.mark.parametrize(
    "input,suffix,expected",
    [
        ("test", "suffix", "testsuffix"),
        (["hello", "world"], "suffix", ["hellosuffix", "worldsuffix"]),
    ],
)
def test_add_suffix(
    input: str | list[str], suffix: str, expected: str | list[str]
) -> None:
    assert add_suffix(input, suffix) == expected


@pytest.mark.parametrize(
    "input,expected",
    [
        ("<a>Test</a><br />Line 2<p>Line 3</p>", ["Test", "Line 2", "Line 3"]),
        ("Test", ["Test"]),
        ("Line 2", ["Line 2"]),
        ("Line 3", ["Line 3"]),
    ],
)
def test_extract_text_from_html(input: str, expected: list[str]) -> None:
    assert extract_text_from_html(input) == expected


@pytest.mark.parametrize(
    "input,expected",
    [
        ('{"key": "value"}\n{"key": "value"}\n', [{"key": "value"}, {"key": "value"}]),
        ('{"key": "value"}\n', [{"key": "value"}]),
        ('{"key": "value"}', [{"key": "value"}]),
        ('{"key": "value"}\n{"key": "value"}', [{"key": "value"}, {"key": "value"}]),
    ],
)
def test_deserialize_ndjson(input, expected):
    assert deserialize_ndjson(input) == expected


@pytest.mark.parametrize(
    "input_val,expected",
    [
        (True, True),
        (False, False),
        ("true", True),
        ("TRUE", True),
        ("1", True),
        ("false", False),
        ("FALSE", False),
        ("0", False),
        (1, True),
        (0, False),
        ([], False),
        ([1], True),
    ],
)
def test_bool(input_val: Any, expected: bool) -> None:
    assert _bool(input_val) == expected


@pytest.mark.parametrize(
    "template,values,expected",
    [
        ("Hello {}", ["World"], "Hello World"),
        ("{} {} {}", ["a", "b", "c"], "a b c"),
        ("Value: {:.2f}", [3.14159], "Value: 3.14"),
    ],
)
def test_format_string(template: str, values: list[Any], expected: str) -> None:
    assert format_string(template, *values) == expected


@pytest.mark.parametrize(
    "invalid_input,decode_func",
    [
        ("invalid base64", b64_to_str),
        ("invalid base64url", b64url_to_str),
    ],
)
def test_base64_invalid_input(invalid_input: str, decode_func) -> None:
    with pytest.raises(ValueError):
        decode_func(invalid_input)


@pytest.mark.parametrize(
    "input_val,timezone,expected",
    [
        # UTC timestamp for 2021-01-01 00:00:00
        (
            1609459200,
            "UTC",
            datetime(2021, 1, 1, 0, 0, tzinfo=UTC),
        ),
        ("2021-01-01T00:00:00", None, datetime(2021, 1, 1, 0, 0)),
        # ISO string with timezone
        ("2021-01-01T00:00:00+00:00", None, datetime(2021, 1, 1, 0, 0, tzinfo=UTC)),
        # ISO string without timezone
        ("2021-01-01T00:00:00", "UTC", datetime(2021, 1, 1, 0, 0, tzinfo=UTC)),
        # ISO date only
        ("2021-01-01", None, datetime(2021, 1, 1, 0, 0)),
        # Datetime object
        (datetime(2021, 1, 1, 0, 0), None, datetime(2021, 1, 1, 0, 0)),
    ],
)
def test_to_datetime(input_val: Any, timezone: str, expected: datetime) -> None:
    assert to_datetime(input_val, timezone) == expected


@pytest.mark.parametrize(
    "input",
    [
        # US mm/dd/yyyy format
        "1/1/2021",
        # ISO 8601 string with invalid date
        "2021-02-31T00:00:00",
    ],
)
def test_to_datetime_invalid_date_string(input: str) -> None:
    with pytest.raises(ValueError):
        to_datetime(input)


@pytest.mark.parametrize(
    "pattern,text,expected",
    [
        (r"\d+", "abc123def", "123"),
        (r"[a-z]+", "ABC123def", "def"),
        (r"test", "no match", None),
    ],
)
def test_regex_extract(pattern: str, text: str, expected: str | None) -> None:
    assert regex_extract(pattern, text) == expected


@pytest.mark.parametrize(
    "pattern,text,expected",
    [
        (r"^test", "test123", True),
        (r"^test", "123test", False),
        (r"\d+", "123", True),
        (r"[A-Z]+", "abc", False),
    ],
)
def test_regex_match(pattern: str, text: str, expected: bool) -> None:
    assert regex_match(pattern, text) == expected
    assert regex_not_match(pattern, text) == (not expected)


def test_generate_uuid() -> None:
    uuid1 = generate_uuid()
    uuid2 = generate_uuid()
    assert isinstance(uuid1, str)
    assert len(uuid1) == 36  # Standard UUID length
    assert uuid1 != uuid2  # Should generate unique values


@pytest.mark.parametrize(
    "ipv4,subnet,expected",
    [
        ("192.168.1.1", "192.168.1.0/24", True),
        ("192.168.1.1", "192.168.2.0/24", False),
        ("10.0.0.1", "10.0.0.0/8", True),
        ("172.16.0.1", "192.168.0.0/16", False),
    ],
)
def test_ipv4_in_subnet(ipv4: str, subnet: str, expected: bool) -> None:
    assert ipv4_in_subnet(ipv4, subnet) == expected


@pytest.mark.parametrize(
    "ipv6,subnet,expected",
    [
        ("2001:db8::1", "2001:db8::/32", True),
        ("2001:db8::1", "2001:db9::/32", False),
        ("fe80::1", "fe80::/10", True),
        ("2001:db8::1", "fe80::/10", False),
    ],
)
def test_ipv6_in_subnet(ipv6: str, subnet: str, expected: bool) -> None:
    assert ipv6_in_subnet(ipv6, subnet) == expected


@pytest.mark.parametrize(
    "ip,expected",
    [
        ("192.168.1.1", False),  # Private
        ("10.0.0.1", False),  # Private
        ("172.16.0.1", False),  # Private
        ("8.8.8.8", True),  # Public
        ("1.1.1.1", True),  # Public
    ],
)
def test_ipv4_is_public(ip: str, expected: bool) -> None:
    assert ipv4_is_public(ip) == expected


@pytest.mark.parametrize(
    "ip,expected",
    [
        ("fe80::1", False),  # Link-local
        ("fc00::1", False),  # Unique local
        ("2001:db8::1", False),  # Documentation prefix (not public)
        ("2606:4700:4700::1111", True),  # Public (Cloudflare DNS)
        ("2404:6800:4000::1", True),  # Public (Google)
    ],
)
def test_ipv6_is_public(ip: str, expected: bool) -> None:
    assert ipv6_is_public(ip) == expected


def test_check_ip_version() -> None:
    assert check_ip_version("192.168.1.1") == 4
    assert check_ip_version("2001:db8::1") == 6
    with pytest.raises(ValueError):
        check_ip_version("invalid-ip")


@pytest.mark.parametrize(
    "func,a,b,expected",
    [
        (less_than, 1, 2, True),
        (less_than, 2, 2, False),
        (less_than, 3, 2, False),
        (less_than, "a", "b", True),
        (less_than, "b", "a", False),
        (less_than, 1.5, 2.5, True),
        (greater_than, 2, 1, True),
        (greater_than, 2, 2, False),
        (greater_than, 1, 2, False),
        (greater_than_or_equal, 2, 1, True),
        (greater_than_or_equal, 2, 2, True),
        (greater_than_or_equal, 1, 2, False),
        (less_than_or_equal, 1, 2, True),
        (less_than_or_equal, 2, 2, True),
        (less_than_or_equal, 3, 2, False),
    ],
)
def test_comparison_operations(func, a: Any, b: Any, expected: bool) -> None:
    assert func(a, b) == expected


@pytest.mark.parametrize(
    "func,value,expected",
    [
        (is_null, None, True),
        (is_null, "test", False),
        (not_null, None, False),
        (not_null, "test", True),
        (is_empty, "", True),
        (is_empty, [], True),
        (is_empty, {}, True),
        (is_empty, "test", False),
        (is_empty, [1], False),
        (not_empty, "", False),
        (not_empty, [], False),
        (not_empty, {}, False),
        (not_empty, "test", True),
        (not_empty, [1], True),
    ],
)
def test_null_and_empty_checks(func, value: Any, expected: bool) -> None:
    assert func(value) == expected


@pytest.mark.parametrize(
    "func,a,b,expected",
    [
        (is_equal, 1, 1, True),
        (is_equal, "test", "test", True),
        (is_equal, 1, 2, False),
        (not_equal, 1, 2, True),
        (not_equal, "test", "test", False),
    ],
)
def test_equality(func, a: Any, b: Any, expected: bool) -> None:
    assert func(a, b) == expected


@pytest.mark.parametrize(
    "func,a,b,expected",
    [
        (contains, 2, [1, 2, 3], True),
        (contains, "el", "hello", True),
        (contains, 4, [1, 2, 3], False),
        (does_not_contain, 4, [1, 2, 3], True),
        (does_not_contain, "x", "hello", True),
        (does_not_contain, 2, [1, 2, 3], False),
    ],
)
def test_contains(func, a: Any, b: Any, expected: bool) -> None:
    assert func(a, b) == expected


@pytest.mark.parametrize(
    "func,input_str,expected",
    [
        (slice_str, ("hello", 1, 3), "ell"),
        (format_string, ("Hello {}", "World"), "Hello World"),
        (lowercase, "HELLO", "hello"),
        (uppercase, "hello", "HELLO"),
        (capitalize, "hello world", "Hello world"),
        (titleize, "hello world", "Hello World"),
        (strip, ("  hello  ", " "), "hello"),
    ],
)
def test_string_operations(func, input_str: str | tuple, expected: str) -> None:
    """Test string manipulation functions."""
    if func in (slice_str, format_string, strip):
        assert func(*input_str) == expected
    else:
        assert func(input_str) == expected


def test_split() -> None:
    assert split("a,b,c", ",") == ["a", "b", "c"]
    assert split("a b c", " ") == ["a", "b", "c"]  # default whitespace splitting
    assert split("a||b||c", "||") == ["a", "b", "c"]


def test_sum_() -> None:
    assert sum_([1, 2, 3]) == 6
    assert sum_([0.1, 0.2, 0.3]) == pytest.approx(0.6)
    assert sum_([]) == 0  # empty list


def test_mappable_decorator() -> None:
    # Test regular function call
    mapped_add = mappable(add)
    result = mapped_add(2, 3)
    assert result == 5

    # Test mapped function call with scalars
    result = mapped_add.map(2, 3)
    assert result == [5]

    # Test mapped function call with sequences
    result = mapped_add.map([1, 2, 3], [4, 5, 6])
    assert result == [5, 7, 9]

    # Test mapped function with mixed scalar and sequence
    result = mapped_add.map([1, 2, 3], 1)
    assert result == [2, 3, 4]


def test_cast_operations() -> None:
    assert cast("123", "int") == 123
    assert cast("123.45", "float") == 123.45
    assert cast("true", "bool") is True
    assert isinstance(cast("2023-01-01T00:00:00", "datetime"), datetime)

    with pytest.raises(ValueError):
        cast("123", "invalid_type")


def test_build_lambda() -> None:
    add_one = _build_safe_lambda("lambda x: x + 1")
    assert add_one(1) == 2


def test_build_lambda_catches_restricted_nodes() -> None:
    with pytest.raises(ValueError) as e:
        _build_safe_lambda("lambda x: import os")
        assert "Expression contains restricted symbols" in str(e)

    with pytest.raises(ValueError) as e:
        _build_safe_lambda("import sys")
        assert "Expression contains restricted symbols" in str(e)

    with pytest.raises(ValueError) as e:
        _build_safe_lambda("lambda x: locals()")
        assert "Expression contains restricted symbols" in str(e)

    with pytest.raises(ValueError) as e:
        _build_safe_lambda("x + 1")
        assert "Expression must be a lambda function" in str(e)


def test_filter_() -> None:
    """Test the filter_ function with various conditions."""
    # Test basic filtering
    assert filter_([1, 2, 3, 4, 5], "lambda x: x % 2 == 0") == [2, 4]
    assert filter_(["a", "bb", "ccc"], "lambda x: len(x) > 1") == ["bb", "ccc"]

    # Test with complex objects
    data = [{"value": 1}, {"value": 2}, {"value": 3}]
    assert filter_(data, "lambda x: x['value'] > 1") == [{"value": 2}, {"value": 3}]

    # Test with empty result
    assert filter_([1, 2, 3], "lambda x: x > 10") == []

    # Test error cases
    with pytest.raises(SyntaxError):
        filter_([1, 2, 3], "not a lambda")
    with pytest.raises(ValueError):
        filter_([1, 2, 3], "lambda x: import os")


@pytest.mark.parametrize(
    "func,date_input,format,expected",
    [
        # Month tests
        (get_month, datetime(2024, 1, 1), "number", 1),
        (get_month, datetime(2024, 12, 1), "number", 12),
        (get_month, datetime(2024, 1, 1), "full", "January"),
        (get_month, datetime(2024, 12, 1), "full", "December"),
        (get_month, datetime(2024, 1, 1), "short", "Jan"),
        (get_month, datetime(2024, 12, 1), "short", "Dec"),
        # Day of week tests
        (get_day_of_week, datetime(2024, 3, 18), "number", 0),  # Monday
        (get_day_of_week, datetime(2024, 3, 24), "number", 6),  # Sunday
        (get_day_of_week, datetime(2024, 3, 18), "full", "Monday"),
        (get_day_of_week, datetime(2024, 3, 24), "full", "Sunday"),
        (get_day_of_week, datetime(2024, 3, 18), "short", "Mon"),
        (get_day_of_week, datetime(2024, 3, 24), "short", "Sun"),
    ],
)
def test_date_formatters(
    func, date_input: datetime, format: str, expected: int | str
) -> None:
    assert func(date_input, format) == expected
    # Test invalid format
    with pytest.raises(ValueError):
        func(date_input, "invalid")


@pytest.mark.parametrize(
    "func,input_str,prefix_suffix,expected",
    [
        (startswith, "Hello World", "Hello", True),
        (startswith, "Hello World", "World", False),
        (endswith, "Hello World", "World", True),
        (endswith, "Hello World", "Hello", False),
        (startswith, "", "", True),
        (endswith, "", "", True),
        (startswith, "", "x", False),
        (endswith, "", "x", False),
    ],
)
def test_string_boundary_functions(
    func, input_str: str, prefix_suffix: str, expected: bool
) -> None:
    assert func(input_str, prefix_suffix) == expected


@pytest.mark.parametrize(
    "func,input_val,expected",
    [
        (get_day, datetime(2024, 3, 1), 1),
        (get_day, datetime(2024, 3, 31), 31),
        (get_hour, datetime(2024, 3, 15, 23), 23),
        (get_minute, datetime(2024, 3, 15, 12, 59), 59),
        (get_second, datetime(2024, 3, 15, 12, 30, 45), 45),
        (get_year, datetime(2024, 3, 15), 2024),
        (get_month, datetime(2024, 1, 1), 1),  # Using number format
        (get_month, datetime(2024, 12, 1), 12),  # Using number format
    ],
)
def test_date_component_getters(func, input_val: datetime, expected: int) -> None:
    """Test all date/time component getter functions."""
    if func == get_month:
        assert func(input_val, "number") == expected
    else:
        assert func(input_val) == expected


@pytest.mark.parametrize(
    "func,input_val,expected",
    [
        (create_days, 1, timedelta(days=1)),
        (create_days, 0.5, timedelta(hours=12)),
        (create_hours, 24, timedelta(days=1)),
        (create_hours, 1.5, timedelta(minutes=90)),
        (create_minutes, 60, timedelta(hours=1)),
        (create_minutes, 1.5, timedelta(seconds=90)),
        (create_seconds, 3600, timedelta(hours=1)),
        (create_seconds, 90, timedelta(seconds=90)),
        (create_weeks, 1, timedelta(weeks=1)),
        (create_weeks, 0.5, timedelta(days=3.5)),
    ],
)
def test_time_interval_creators(func, input_val: float, expected: timedelta) -> None:
    """Test all time interval creation functions."""
    assert func(input_val) == expected


@pytest.mark.parametrize(
    "func,start,end,expected",
    [
        (weeks_between, datetime(2024, 1, 1), datetime(2024, 1, 8), 1.0),
        (weeks_between, datetime(2024, 1, 1), datetime(2024, 1, 15), 2.0),
        (days_between, datetime(2024, 1, 1), datetime(2024, 1, 2), 1.0),
        (days_between, datetime(2024, 1, 1, 12), datetime(2024, 1, 2), 0.5),
        (hours_between, datetime(2024, 1, 1), datetime(2024, 1, 1, 6), 6.0),
        (minutes_between, datetime(2024, 1, 1), datetime(2024, 1, 1, 0, 30), 30.0),
        (seconds_between, datetime(2024, 1, 1), datetime(2024, 1, 1, 0, 0, 30), 30.0),
    ],
)
def test_time_between_calculations(
    func, start: datetime, end: datetime, expected: float
) -> None:
    assert func(start, end) == pytest.approx(expected)


@pytest.mark.parametrize(
    "func,input_dict,expected",
    [
        (dict_keys, {"a": 1, "b": 2, "c": 3}, {"a", "b", "c"}),
        (dict_values, {"a": 1, "b": 2, "c": 3}, {1, 2, 3}),
        (dict_keys, {}, set()),  # Empty dict
        (dict_values, {}, set()),  # Empty dict
    ],
)
def test_dict_operations(func, input_dict: dict, expected: set) -> None:
    assert set(func(input_dict)) == expected

    # Test with non-dict input
    with pytest.raises(AttributeError):
        func("not a dict")  # type: ignore


@pytest.mark.parametrize(
    "lambda_str,error_type,error_message",
    [
        ("lambda x: import os", ValueError, "Expression contains restricted symbols"),
        ("import sys", ValueError, "Expression contains restricted symbols"),
        ("lambda x: locals()", ValueError, "Expression contains restricted symbols"),
        ("x + 1", ValueError, "Expression must be a lambda function"),
        ("lambda x: globals()", ValueError, "Expression contains restricted symbols"),
        ("lambda x: eval('1+1')", ValueError, "Expression contains restricted symbols"),
    ],
)
def test_build_lambda_errors(
    lambda_str: str, error_type: type[Exception], error_message: str
) -> None:
    with pytest.raises(error_type) as e:
        _build_safe_lambda(lambda_str)
        assert error_message in str(e)


@pytest.mark.parametrize(
    "func,a,b,expected",
    [
        (add, 2, 3, 5),
        (sub, 5, 3, 2),
        (mul, 4, 3, 12),
        (div, 6, 2, 3.0),
        (mod, 7, 3, 1),
        (pow, 2, 3, 8),
        # Edge cases
        (div, 5, 2, 2.5),
        (mod, 5, 2, 1),
        (pow, 3, 0, 1),
    ],
)
def test_math_operations(func, a: Any, b: Any, expected: Any) -> None:
    assert func(a, b) == expected


@pytest.mark.parametrize(
    "func,a,b,expected",
    [
        (and_, True, True, True),
        (and_, True, False, False),
        (or_, False, True, True),
        (or_, False, False, False),
        (not_, True, None, False),
        (not_, False, None, True),
    ],
)
def test_logical_operations(func, a: bool, b: Any, expected: bool) -> None:
    if b is None:
        assert func(a) == expected
    else:
        assert func(a, b) == expected


@pytest.mark.parametrize(
    "input_data,expected",
    [
        ({"a": 1, "b": 2}, {"a": 1, "b": 2}),
        ([1, 2, 3], [1, 2, 3]),
        ("test", "test"),
        (123, 123),
    ],
)
def test_serialize_to_json(input_data: Any, expected: Any) -> None:
    result = serialize_to_json(input_data)
    assert orjson.loads(result) == expected


@pytest.mark.parametrize(
    "input_data,expected",
    [
        ({"a": 1}, '{\n  "a": 1\n}'),
        ([1, 2], "[\n  1,\n  2\n]"),
        ("test", '"test"'),
    ],
)
def test_prettify_json_str(input_data: Any, expected: str) -> None:
    assert prettify_json_str(input_data) == expected


@pytest.mark.parametrize(
    "collections,expected",
    [
        (([1, 2], [2, 3]), [1, 2, 3]),
        (([1], [2], [3]), [1, 2, 3]),
        (([], [1, 2]), [1, 2]),
        (([1, 2], []), [1, 2]),
    ],
)
def test_union(collections: tuple[list, ...], expected: list) -> None:
    assert sorted(union(*collections)) == sorted(expected)


@pytest.mark.parametrize(
    "iterables,expected",
    [
        (([1, 2], [3, 4]), [(1, 3), (2, 4)]),
        (([1], [2, 3]), [(1, 2)]),
        (([], [1, 2]), []),
    ],
)
def test_zip_iterables(iterables: tuple[list, ...], expected: list[tuple]) -> None:
    assert zip_iterables(*iterables) == expected


@pytest.mark.parametrize(
    "iterables,expected",
    [
        (([1, 2], [3, 4]), [(1, 3), (1, 4), (2, 3), (2, 4)]),
        (([1], [2]), [(1, 2)]),
        (([], [1, 2]), []),
    ],
)
def test_iter_product(iterables: tuple[list, ...], expected: list[tuple]) -> None:
    assert iter_product(*iterables) == expected


@pytest.mark.parametrize(
    "dt,timezone,expected_range",
    [
        # America/New_York varies between UTC-5 (EST) and UTC-4 (EDT)
        (datetime(2024, 1, 1, tzinfo=UTC), "America/New_York", (-5, -4)),
        # UTC is always +0
        (datetime(2024, 1, 1, tzinfo=UTC), "UTC", (0, 0)),
        # Asia/Tokyo is always UTC+9
        (datetime(2024, 1, 1, tzinfo=UTC), "Asia/Tokyo", (9, 9)),
    ],
)
def test_set_timezone(
    dt: datetime, timezone: str, expected_range: tuple[int, int]
) -> None:
    """Test timezone conversion, accounting for possible DST variations."""
    result = set_timezone(dt, timezone)
    offset = result.utcoffset()
    assert offset is not None
    offset_hours = offset.total_seconds() / 3600
    min_offset, max_offset = expected_range
    assert min_offset <= offset_hours <= max_offset, (
        f"Offset {offset_hours} not in expected range [{min_offset}, {max_offset}]"
    )


@pytest.mark.parametrize(
    "dt",
    [
        datetime(2024, 1, 1, tzinfo=UTC),
        datetime(2024, 1, 1),
    ],
)
def test_unset_timezone(dt: datetime) -> None:
    assert unset_timezone(dt) == dt.replace(tzinfo=None)


@pytest.mark.parametrize(
    "input_str,expected",
    [
        ("admin+tracecat1@gmail.com", "admin%2Btracecat1%40gmail.com"),
        ("admin+tracecat1-org@gmail.com", "admin%2Btracecat1-org%40gmail.com"),
    ],
)
def test_url_encode(input_str: str, expected: str) -> None:
    assert url_encode(input_str) == expected


@pytest.mark.parametrize(
    "input_str,expected",
    [
        ("Hello, World!", "SGVsbG8sIFdvcmxkIQ=="),
        ("", ""),
        ("Special chars: !@#$%^&*()", "U3BlY2lhbCBjaGFyczogIUAjJCVeJiooKQ=="),
    ],
)
def test_str_to_b64(input_str: str, expected: str) -> None:
    assert str_to_b64(input_str) == expected
    # Test URL-safe version
    url_result = str_to_b64url(input_str)
    assert b64url_to_str(url_result) == input_str


@pytest.mark.parametrize(
    "input_dict,key,expected",
    [
        ({"a": 1}, "a", 1),
        ({"a": None}, "a", None),
        ({}, "a", None),
        ({1: "one"}, 1, "one"),
        ({(1, 2): "tuple"}, (1, 2), "tuple"),
    ],
)
def test_dict_lookup(input_dict: dict, key: Any, expected: Any) -> None:
    assert dict_lookup(input_dict, key) == expected


@pytest.mark.parametrize(
    "input_iterables,expected",
    [
        # Basic flattening
        ([[1, 2], [3, 4]], [1, 2, 3, 4]),
        # Nested lists
        ([[1, [2, 3]], [4]], [1, 2, 3, 4]),
        # Empty cases
        ([], []),  # Empty list
        ([[]], []),  # List containing empty list
        # Different element types
        ([["a", "b"], ["c"]], ["a", "b", "c"]),  # String elements
        ([[1, 2], [], [3]], [1, 2, 3]),  # Some empty sublists
        # Preserve non-list types
        ([[{"a": 1}], [{"b": 2}]], [{"a": 1}, {"b": 2}]),  # Dict elements
        ([[(1, 2)], [(3, 4)]], [1, 2, 3, 4]),  # Tuples get flattened
        # Deep nesting
        ([[1, [2, [3, 4]]], [5]], [1, 2, 3, 4, 5]),
    ],
)
def test_flatten(input_iterables: list, expected: list) -> None:
    """Test flatten function with various input types and structures.
    The function recursively flattens all sequences (including tuples) into a single list.
    """
    assert flatten(input_iterables) == expected

    # Test with non-iterable input
    with pytest.raises((TypeError, AttributeError)):
        flatten(123)  # type: ignore


@pytest.mark.parametrize(
    "items,collection,python_lambda,expected",
    [
        ([1, 2, 3], [2, 3, 4], None, [2, 3]),
        # Empty intersection
        ([1, 2], [3, 4], None, []),
        # Empty inputs
        ([], [1, 2], None, []),
        ([1, 2], [], None, []),
        # Duplicate values
        ([1, 1, 2], [1, 2, 2], None, [1, 2]),
        # String values
        (["a", "b"], ["b", "c"], None, ["b"]),
        # With lambda transformation
        ([1, 2, 3], [2, 4, 6], "lambda x: x * 2", [1, 2, 3]),
        # Lambda with string manipulation
        (
            ["hello", "world"],
            ["HELLO", "WORLD"],
            "lambda x: x.upper()",
            ["hello", "world"],
        ),
        # Complex objects
        ([(1, 2), (3, 4)], [(1, 2), (5, 6)], None, [(1, 2)]),
    ],
)
def test_intersect(
    items: list, collection: list, python_lambda: str | None, expected: list
) -> None:
    """Test the intersect function with various inputs and transformations."""
    result = intersect(items, collection, python_lambda)
    # Sort the results to ensure consistent comparison
    assert sorted(result) == sorted(expected)


@pytest.mark.parametrize(
    "start,end,step,expected",
    [
        (0, 5, 1, [0, 1, 2, 3, 4]),  # Basic range
        (1, 10, 2, [1, 3, 5, 7, 9]),  # Range with step
        (5, 0, -1, [5, 4, 3, 2, 1]),  # Descending range
        (0, 0, 1, []),  # Empty range
        (-5, 5, 2, [-5, -3, -1, 1, 3]),  # Range with negative start
        (10, 5, -2, [10, 8, 6]),  # Descending range with step
    ],
)
def test_create_range(start: int, end: int, step: int, expected: list[int]) -> None:
    """Test create_range function with various inputs.

    Tests:
    - Basic ascending range
    - Range with custom step size
    - Descending range
    - Empty range
    - Range with negative numbers
    - Descending range with custom step
    """
    result = create_range(start, end, step)
    assert list(result) == expected

    # Test invalid step
    with pytest.raises(ValueError):
        create_range(0, 5, 0)  # Step cannot be 0


@pytest.mark.parametrize(
    "a,b,expected",
    [
        ([1, 2, 3], [2, 3, 4], [1]),  # Basic difference
        ([1, 2, 2], [2], [1]),  # Duplicates in first sequence
        ([], [1, 2], []),  # Empty first sequence
        ([1, 2], [], [1, 2]),  # Empty second sequence
        (["a", "b"], ["b", "c"], ["a"]),  # String elements
    ],
)
def test_difference(a: Sequence[Any], b: Sequence[Any], expected: list[Any]) -> None:
    """Test set difference between two sequences."""
    assert sorted(difference(a, b)) == sorted(expected)


@pytest.mark.parametrize(
    "input_val,unit,expected",
    [
        (
            1609459200,
            "s",
            datetime(2021, 1, 1, 0, 0, tzinfo=UTC),
        ),  # 2021-01-01 00:00:00
        (
            1609459200000,
            "ms",
            datetime(2021, 1, 1, 0, 0, tzinfo=UTC),
        ),  # Same time in milliseconds
        (
            1672531200,
            "s",
            datetime(2023, 1, 1, 0, 0, tzinfo=UTC),
        ),  # 2023-01-01 00:00:00
        (
            1672531200000,
            "ms",
            datetime(2023, 1, 1, 0, 0, tzinfo=UTC),
        ),  # Same time in milliseconds
    ],
)
def test_from_timestamp(input_val: int, unit: str, expected: datetime) -> None:
    assert from_timestamp(input_val, unit) == expected


@pytest.mark.parametrize(
    "input_val,unit,expected",
    [
        (
            datetime(2021, 1, 1, 0, 0, tzinfo=UTC),
            "s",
            1609459200,
        ),  # 2021-01-01 00:00:00
        (
            datetime(2021, 1, 1, 0, 0, tzinfo=UTC),
            "ms",
            1609459200000,
        ),  # Same time in milliseconds
        (
            datetime(2023, 1, 1, 0, 0, tzinfo=UTC),
            "s",
            1672531200,
        ),  # 2023-01-01 00:00:00
        (
            datetime(2023, 1, 1, 0, 0, tzinfo=UTC),
            "ms",
            1672531200000,
        ),  # Same time in milliseconds
        ("2021-01-01T00:00:00", "s", 1609459200),  # String input
        ("2023-01-01T00:00:00", "ms", 1672531200000),  # String input with ms
    ],
)
def test_to_timestamp(input_val: datetime | str, unit: str, expected: int) -> None:
    assert to_timestamp(input_val, unit) == expected


@pytest.mark.parametrize(
    "input_str,format_str,expected",
    [
        (
            "2021-01-01 00:00:00",
            "%Y-%m-%d %H:%M:%S",
            datetime(2021, 1, 1, 0, 0, 0),
        ),
        (
            "01/01/2021 15:30",
            "%d/%m/%Y %H:%M",
            datetime(2021, 1, 1, 15, 30),
        ),
        (
            "2023-12-31",
            "%Y-%m-%d",
            datetime(2023, 12, 31),
        ),
    ],
)
def test_parse_datetime(input_str: str, format_str: str, expected: datetime) -> None:
    assert parse_datetime(input_str, format_str) == expected

    # Test invalid format
    with pytest.raises(ValueError):
        parse_datetime(input_str, "invalid_format")


@pytest.mark.parametrize(
    "input_val,format_str,expected",
    [
        (
            datetime(2021, 1, 1, 0, 0),
            "%Y-%m-%d %H:%M:%S",
            "2021-01-01 00:00:00",
        ),
        (
            datetime(2021, 1, 1, 15, 30),
            "%d/%m/%Y %H:%M",
            "01/01/2021 15:30",
        ),
        (
            "2021-01-01T00:00:00",  # String input
            "%Y-%m-%d",
            "2021-01-01",
        ),
        # With timezone
        (
            datetime(2021, 1, 1, 0, 0, tzinfo=UTC),
            "%Y-%m-%d %H:%M:%S",
            "2021-01-01 00:00:00",
        ),
        # With timezone in ISO 8601 datetime string
        (
            "2021-01-01T00:00:00+00:00",
            "%Y-%m-%d %H:%M:%S",
            "2021-01-01 00:00:00",
        ),
    ],
)
def test_format_datetime(
    input_val: datetime | str, format_str: str, expected: str
) -> None:
    assert format_datetime(input_val, format_str) == expected
