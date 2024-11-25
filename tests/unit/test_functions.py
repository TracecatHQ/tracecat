from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from tracecat.expressions.functions import (
    # Core/Utils
    _bool,
    _build_safe_lambda,
    # Math Operations
    add,
    # Logical Operations
    and_,
    # Misc
    apply,
    # Encoding/Decoding
    b64_to_str,
    b64url_to_str,
    # String Operations
    capitalize,
    cast,
    # IP Address Operations
    check_ip_version,
    concat_strings,
    # Comparison
    contains,
    # Time/Date Operations
    create_days,
    create_hours,
    create_minutes,
    create_seconds,
    create_weeks,
    # Collection Operations
    custom_chain,
    days_between,
    # JSON Operations
    deserialize_ndjson,
    dict_keys,
    dict_lookup,
    dict_values,
    div,
    does_not_contain,
    endswith,
    extract_text_from_html,
    filter_,
    flatten,
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
    join_strings,
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
    now,
    or_,
    pow,
    prettify_json_str,
    # Regular Expressions
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
    to_date_string,
    to_datetime,
    to_iso_format,
    to_timestamp_str,
    today,
    union,
    unique_items,
    unset_timezone,
    uppercase,
    utcnow,
    weeks_between,
    zip_iterables,
)


def test_ip_functions():
    assert ipv4_in_subnet("192.168.0.1", "192.168.0.0/24")
    assert ipv4_in_subnet("10.120.100.5", "10.120.0.0/16")
    assert not ipv4_in_subnet("18.140.9.10", "18.140.9.0/30")
    assert not ipv4_is_public("192.168.0.1")
    assert not ipv4_is_public("172.16.0.1")
    assert not ipv4_is_public("127.0.0.1")
    assert ipv4_is_public("172.15.255.255")
    assert not ipv6_in_subnet(
        "2001:db8:85a4:0000:0000:8a2e:0370:7334", "2001:0db8:85a3::/64"
    )
    assert ipv6_in_subnet(
        "2001:db8:85a3:0000:0000:8a2e:0370:7334", "2001:0db8:85a3::/64"
    )
    assert not ipv6_is_public("fd12:3456:789a:1::1")
    assert ipv6_is_public("2607:f8b0:4002:c00::64")


def test_extract_text_from_html():
    assert extract_text_from_html("<a>Test</a><br />Line 2<p>Line 3</p>") == [
        "Test",
        "Line 2",
        "Line 3",
    ]


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


def test_from_timestamp() -> None:
    # Test milliseconds
    assert from_timestamp(1609459200000, "ms") == datetime(2021, 1, 1, 0, 0)
    # Test seconds
    assert from_timestamp(1609459200, "s") == datetime(2021, 1, 1, 0, 0)


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


def test_base64_functions() -> None:
    original = "Hello World!+/="

    # Test standard base64
    b64_encoded = str_to_b64(original)
    assert b64_to_str(b64_encoded) == original

    # Test base64url
    b64url_encoded = str_to_b64url(original)
    assert b64url_to_str(b64url_encoded) == original

    # Test empty string
    assert b64_to_str(str_to_b64("")) == ""
    assert b64url_to_str(str_to_b64url("")) == ""

    # Test invalid input
    with pytest.raises(ValueError):
        b64_to_str("invalid base64")
    with pytest.raises(ValueError):
        b64url_to_str("invalid base64url")


@pytest.mark.parametrize(
    "input_val,expected",
    [
        (1609459200, datetime(2021, 1, 1, 0, 0)),
        ("2021-01-01T00:00:00", datetime(2021, 1, 1, 0, 0)),
        (datetime(2021, 1, 1, 0, 0), datetime(2021, 1, 1, 0, 0)),
    ],
)
def test_to_datetime(input_val: Any, expected: datetime) -> None:
    assert to_datetime(input_val) == expected


def test_to_datetime_invalid() -> None:
    with pytest.raises(ValueError):
        to_datetime([])


def test_slice_str() -> None:
    assert slice_str("Hello World", 0, 5) == "Hello"
    assert slice_str("Hello World", 6, 5) == "World"
    assert slice_str("Hello", 1, 2) == "el"


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


def test_collection_functions() -> None:
    # Test flatten
    assert flatten([[1, 2], [3, 4]]) == [1, 2, 3, 4]
    assert flatten([[1, [2, 3]], [4]]) == [1, 2, 3, 4]

    # Test unique_items
    assert set(unique_items([1, 2, 2, 3, 3, 3])) == {1, 2, 3}

    # Test join_strings
    assert join_strings(["a", "b", "c"], ",") == "a,b,c"

    # Test concat_strings
    assert concat_strings("a", "b", "c") == "abc"


def test_generate_uuid() -> None:
    uuid1 = generate_uuid()
    uuid2 = generate_uuid()
    assert isinstance(uuid1, str)
    assert len(uuid1) == 36  # Standard UUID length
    assert uuid1 != uuid2  # Should generate unique values


def test_json_operations() -> None:
    data = {"name": "test", "value": 123}
    json_str = serialize_to_json(data)
    assert isinstance(json_str, str)
    assert "name" in json_str
    assert "test" in json_str


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
    "func,container,item,expected",
    [
        (contains, [1, 2, 3], 2, True),
        (contains, "hello", "el", True),
        (contains, [1, 2, 3], 4, False),
        (does_not_contain, [1, 2, 3], 4, True),
        (does_not_contain, "hello", "x", True),
        (does_not_contain, [1, 2, 3], 2, False),
    ],
)
def test_contains(func, container: Any, item: Any, expected: bool) -> None:
    assert func(container, item) == expected


@pytest.mark.parametrize(
    "func,input_str,expected",
    [
        (slice_str, ("hello", 1, 3), "ell"),
        (format_string, ("Hello {}", "World"), "Hello World"),
        (lowercase, "HELLO", "hello"),
        (uppercase, "hello", "HELLO"),
        (capitalize, "hello world", "Hello world"),
        (titleize, "hello world", "Hello World"),
        (strip, "  hello  ", "hello"),
    ],
)
def test_string_operations(func, input_str: str | tuple, expected: str) -> None:
    """Test string manipulation functions."""
    if func == slice_str:
        assert func(*input_str) == expected
    elif func == format_string:
        assert func(*input_str) == expected
    else:
        assert func(input_str) == expected


def test_split() -> None:
    assert split("a,b,c", ",") == ["a", "b", "c"]
    assert split("a b c", " ") == ["a", "b", "c"]  # default whitespace splitting
    assert split("a||b||c", "||") == ["a", "b", "c"]


def test_iter_product() -> None:
    assert list(iter_product([1, 2], [3, 4])) == [(1, 3), (1, 4), (2, 3), (2, 4)]
    assert list(iter_product("ab", "cd")) == [
        ("a", "c"),
        ("a", "d"),
        ("b", "c"),
        ("b", "d"),
    ]


def test_zip_iterables() -> None:
    assert list(zip_iterables([1, 2, 3], ["a", "b", "c"])) == [
        (1, "a"),
        (2, "b"),
        (3, "c"),
    ]
    assert list(zip_iterables([1, 2], ["a", "b", "c"])) == [
        (1, "a"),
        (2, "b"),
    ]  # truncates to shortest


def test_sum_() -> None:
    assert sum_([1, 2, 3]) == 6
    assert sum_([0.1, 0.2, 0.3]) == pytest.approx(0.6)
    assert sum_([]) == 0  # empty list


def test_math_operations() -> None:
    # Test basic arithmetic operations
    assert add(2, 3) == 5
    assert sub(5, 3) == 2
    assert mul(4, 3) == 12
    assert div(6, 2) == 3.0
    assert mod(7, 3) == 1
    assert pow(2, 3) == 8

    # Test division by zero
    with pytest.raises(ZeroDivisionError):
        div(1, 0)
    with pytest.raises(ZeroDivisionError):
        mod(1, 0)


def test_logical_operations() -> None:
    assert and_(True, True) is True
    assert and_(True, False) is False
    assert or_(True, False) is True
    assert or_(False, False) is False
    assert not_(True) is False
    assert not_(False) is True


def test_sequence_operations() -> None:
    # Test intersect
    assert set(intersect([1, 2, 3], [2, 3, 4])) == {2, 3}
    assert set(intersect([1, 2, 3], [4, 5, 6])) == set()

    # Test union
    assert set(union([1, 2, 3], [3, 4, 5])) == {1, 2, 3, 4, 5}
    assert set(union([1, 2], [3, 4])) == {1, 2, 3, 4}


def test_time_operations() -> None:
    # Test now() returns a datetime
    assert isinstance(now(), datetime)

    # Test create_minutes
    delta = create_minutes(30)
    assert isinstance(delta, timedelta)
    assert delta.total_seconds() == 1800

    # Test to_date_string
    dt = datetime(2023, 1, 1, 12, 30)
    assert to_date_string(dt, "%Y-%m-%d %H:%M") == "2023-01-01 12:30"

    # Test to_iso_format
    assert to_iso_format(dt) == "2023-01-01T12:30:00"

    # Test to_timestamp_str
    timestamp = to_timestamp_str(dt)
    assert isinstance(timestamp, float)


def test_html_text_extraction() -> None:
    html = """
    <html>
        <body>
            <h1>Title</h1>
            <p>Paragraph 1</p>
            <p>Paragraph 2</p>
        </body>
    </html>
    """
    result = extract_text_from_html(html)
    assert "Title" in result
    assert "Paragraph 1" in result
    assert "Paragraph 2" in result


def test_ndjson_operations() -> None:
    ndjson_str = '{"name": "John", "age": 30}\n{"name": "Jane", "age": 25}'
    result = deserialize_ndjson(ndjson_str)
    assert len(result) == 2
    assert result[0]["name"] == "John"
    assert result[1]["age"] == 25


def test_json_formatting() -> None:
    data = {"name": "test", "nested": {"key": "value"}}
    pretty = prettify_json_str(data)
    assert isinstance(pretty, str)
    assert "{\n" in pretty
    assert "  " in pretty  # Check indentation


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


def test_apply() -> None:
    """Test the apply function with both scalar and sequence inputs."""
    # Test with scalar input
    assert apply(5, "lambda x: x * 2") == 10
    assert apply("hello", "lambda x: x.upper()") == "HELLO"

    # Test with sequence input
    assert apply([1, 2, 3], "lambda x: x * 2") == [2, 4, 6]
    assert apply(["a", "b", "c"], "lambda x: x.upper()") == ["A", "B", "C"]

    # Test with more complex lambda
    assert apply([{"value": 1}, {"value": 2}], "lambda x: x['value'] * 2") == [2, 4]

    # Test error cases
    with pytest.raises(SyntaxError):
        apply(5, "not a lambda")
    with pytest.raises(ValueError):
        apply(5, "lambda x: import os")


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
    "date_input,format,expected",
    [
        # Test number format (0-6 = Mon-Sun)
        (datetime(2024, 3, 18), "number", 0),
        (datetime(2024, 3, 24), "number", 6),
        # Test full names
        (datetime(2024, 3, 18), "full", "Monday"),
        (datetime(2024, 3, 24), "full", "Sunday"),
        # Test short names
        (datetime(2024, 3, 18), "short", "Mon"),
        (datetime(2024, 3, 24), "short", "Sun"),
        # Test leap year
        (datetime(2024, 2, 29), "number", 3),  # Thursday
        # Edge cases
        (datetime(2024, 12, 31), "number", 1),  # Tuesday at year boundary
        (datetime(2024, 2, 29), "full", "Thursday"),  # Leap year
        (datetime(2025, 2, 28), "short", "Fri"),  # Non-leap year
    ],
)
def test_get_day_of_week(
    date_input: datetime, format: str, expected: int | str
) -> None:
    assert get_day_of_week(date_input, format) == expected  # type: ignore
    # Test invalid format
    with pytest.raises(ValueError):
        get_day_of_week(date_input, "invalid")  # type: ignore


@pytest.mark.parametrize(
    "date_input,format,expected",
    [
        # Test number format (1-12)
        (datetime(2024, 1, 1), "number", 1),
        (datetime(2024, 12, 1), "number", 12),
        # Test full names
        (datetime(2024, 1, 1), "full", "January"),
        (datetime(2024, 12, 1), "full", "December"),
        # Test short names
        (datetime(2024, 1, 1), "short", "Jan"),
        (datetime(2024, 12, 1), "short", "Dec"),
        # Test leap year
        (datetime(2024, 2, 29), "short", "Feb"),
    ],
)
def test_get_month(date_input: datetime, format: str, expected: int | str) -> None:
    assert get_month(date_input, format) == expected  # type: ignore
    # Test invalid format
    with pytest.raises(ValueError):
        get_month(date_input, "invalid")  # type: ignore


def test_string_boundary_functions() -> None:
    """Test string start/end checking functions."""
    # Test startswith
    assert startswith("Hello World", "Hello")
    assert not startswith("Hello World", "World")

    # Test endswith
    assert endswith("Hello World", "World")
    assert not endswith("Hello World", "Hello")

    # Test with empty strings
    assert startswith("", "")
    assert endswith("", "")
    assert not startswith("", "x")
    assert not endswith("", "x")


def test_today_and_now_functions() -> None:
    """Test current time/date functions."""
    # Test today()
    today_result = today()
    assert isinstance(today_result, datetime)
    assert today_result.hour == 0
    assert today_result.minute == 0
    assert today_result.second == 0

    # Test now() and utcnow()
    now_result = now()
    utc_result = utcnow()
    assert isinstance(now_result, datetime)
    assert isinstance(utc_result, datetime)
    assert utc_result.tzinfo == UTC


def test_dict_operations() -> None:
    """Test dictionary operations."""
    test_dict = {"a": 1, "b": 2, "c": 3}

    # Test dict_keys
    assert set(dict_keys(test_dict)) == {"a", "b", "c"}

    # Test dict_values
    assert set(dict_values(test_dict)) == {1, 2, 3}

    # Test dict_lookup (already covered in separate test)

    # Test with empty dict
    empty_dict = {}
    assert list(dict_keys(empty_dict)) == []
    assert list(dict_values(empty_dict)) == []

    # Test with non-dict input
    with pytest.raises(AttributeError):
        dict_keys("not a dict")  # type: ignore


def test_timestamp_conversions() -> None:
    """Test timestamp conversion functions."""
    dt = datetime(2024, 3, 15, 12, 30, 45)

    # Test to_timestamp_str
    timestamp = to_timestamp_str(dt)
    assert isinstance(timestamp, float)

    # Test from_timestamp
    assert from_timestamp(1710500000000, "ms") == datetime(2024, 3, 15, 12, 33, 20)
    assert from_timestamp(1710500000, "s") == datetime(2024, 3, 15, 12, 33, 20)

    # Test invalid unit
    with pytest.raises(ValueError):
        from_timestamp(1710500000, "invalid")  # type: ignore


def test_date_string_formats() -> None:
    """Test date string formatting functions."""
    dt = datetime(2024, 3, 15, 12, 30, 45)

    # Test to_date_string with different formats
    assert to_date_string(dt, "%Y-%m-%d") == "2024-03-15"
    assert to_date_string(dt, "%H:%M:%S") == "12:30:45"
    assert to_date_string(dt, "%Y-%m-%d %H:%M:%S") == "2024-03-15 12:30:45"

    # Test to_iso_format
    assert to_iso_format(dt) == "2024-03-15T12:30:45"

    # Test with timezone
    dt_tz = datetime(2024, 3, 15, 12, 30, 45, tzinfo=UTC)
    assert to_iso_format(dt_tz) == "2024-03-15T12:30:45+00:00"


def test_datetime_parsing() -> None:
    """Test datetime parsing functions."""
    # Test to_datetime with different input types
    assert to_datetime("2024-03-15") == datetime(2024, 3, 15)
    assert to_datetime("2024-03-15T12:30:45") == datetime(2024, 3, 15, 12, 30, 45)
    assert to_datetime(1710500000) == datetime(2024, 3, 15, 12, 33, 20)
    assert to_datetime(datetime(2024, 3, 15)) == datetime(2024, 3, 15)

    # Test invalid inputs
    with pytest.raises(ValueError):
        to_datetime("invalid date")
    with pytest.raises(ValueError):
        to_datetime([])  # type: ignore


@pytest.mark.parametrize(
    "func,input_val,expected",
    [
        (get_day, datetime(2024, 3, 1), 1),
        (get_day, datetime(2024, 3, 31), 31),
        (get_hour, datetime(2024, 3, 15, 23), 23),
        (get_minute, datetime(2024, 3, 15, 12, 59), 59),
        (get_second, datetime(2024, 3, 15, 12, 30, 45), 45),
        (get_year, datetime(2024, 3, 15), 2024),
    ],
)
def test_date_component_getters(func, input_val: datetime, expected: int) -> None:
    """Test all date/time component getter functions."""
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
        (weeks_between, to_datetime("2024-01-01"), to_datetime("2024-01-08"), 1.0),
        (weeks_between, to_datetime("2024-01-01"), to_datetime("2024-01-15"), 2.0),
        (days_between, to_datetime("2024-01-01"), to_datetime("2024-01-02"), 1.0),
        (days_between, to_datetime("2024-01-01 12:00"), to_datetime("2024-01-02"), 0.5),
        (
            hours_between,
            to_datetime("2024-01-01"),
            to_datetime("2024-01-01 06:00"),
            6.0,
        ),
        (
            minutes_between,
            to_datetime("2024-01-01"),
            to_datetime("2024-01-01 00:30"),
            30.0,
        ),
        (
            seconds_between,
            to_datetime("2024-01-01"),
            to_datetime("2024-01-01 00:00:30"),
            30.0,
        ),
    ],
)
def test_time_between_calculations(
    func, start: datetime, end: datetime, expected: float
) -> None:
    """Test all time difference calculation functions."""
    assert func(start, end) == pytest.approx(expected)


def test_timezone_operations() -> None:
    """Test timezone manipulation functions."""
    dt_utc = datetime(2024, 3, 15, 12, 0, tzinfo=UTC)

    # Test set_timezone
    dt_est = set_timezone(dt_utc, "America/New_York")
    assert dt_est.tzinfo is not None
    assert dt_est.hour == 8  # UTC-4 during DST

    dt_tokyo = set_timezone(dt_utc, "Asia/Tokyo")
    assert dt_tokyo.tzinfo is not None
    assert dt_tokyo.hour == 21  # UTC+9

    # Test unset_timezone
    dt_naive = unset_timezone(dt_utc)
    assert dt_naive.tzinfo is None
    assert dt_naive.hour == dt_utc.hour

    # Test with string input
    dt_str = set_timezone(datetime(2024, 3, 15, 12, 0, tzinfo=UTC), "America/New_York")
    assert dt_str.hour == 8

    # Test errors
    with pytest.raises(ValueError):
        set_timezone(dt_utc, "Invalid/Timezone")


@pytest.mark.parametrize(
    "dict_input,key,expected",
    [
        # Basic lookups
        ({"a": 1}, "a", 1),
        ({"a": None}, "a", None),
        # Missing keys
        ({"a": 1}, "b", None),
        # Mixed key types
        ({1: "one", "2": "two"}, 1, "one"),
        ({(1, 2): "tuple"}, (1, 2), "tuple"),
        # Empty cases
        ({}, "a", None),
    ],
)
def test_dict_lookup(dict_input: dict, key: Any, expected: Any) -> None:
    """Test dictionary lookup with various inputs."""
    assert dict_lookup(dict_input, key) == expected


def test_collection_operations() -> None:
    """Test collection manipulation functions."""
    # Test flatten
    assert flatten([[1, 2], [3, 4]]) == [1, 2, 3, 4]
    assert flatten([[1, [2, 3]], [4]]) == [1, 2, 3, 4]

    # Test unique_items
    assert set(unique_items([1, 2, 2, 3, 3, 3])) == {1, 2, 3}

    # Test custom_chain
    assert list(custom_chain([1, 2], [3, 4])) == [1, 2, 3, 4]
    assert list(custom_chain([1, [2, 3]], 4)) == [1, 2, 3, 4]


@pytest.mark.parametrize(
    "func,inputs,expected",
    [
        (contains, (1, [1, 2, 3]), True),
        (contains, (4, [1, 2, 3]), False),
        (does_not_contain, (1, [1, 2, 3]), False),
        (does_not_contain, (4, [1, 2, 3]), True),
        (is_empty, ([],), True),
        (is_empty, ([1],), False),
        (not_empty, ([],), False),
        (not_empty, ([1],), True),
    ],
)
def test_collection_checks(func, inputs: tuple, expected: bool) -> None:
    assert func(*inputs) == expected


def test_collection_transformations() -> None:
    # Test zip_iterables
    assert zip_iterables([1, 2], ["a", "b"]) == [(1, "a"), (2, "b")]
    assert zip_iterables([1], ["a", "b"]) == [(1, "a")]  # Test uneven lengths

    # Test iter_product
    assert iter_product([1, 2], ["a", "b"]) == [(1, "a"), (1, "b"), (2, "a"), (2, "b")]
