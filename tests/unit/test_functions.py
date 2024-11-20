from datetime import datetime, timedelta
from typing import Any

import pytest

from tracecat.expressions.functions import (
    _bool,
    _build_safe_lambda,
    add,
    and_,
    apply,
    b64_to_str,
    cast,
    check_ip_version,
    concat_strings,
    create_minutes,
    deserialize_ndjson,
    dict_keys,
    dict_values,
    div,
    extract_text_from_html,
    filter_,
    flatten,
    format_string,
    from_timestamp,
    generate_uuid,
    intersect,
    ipv4_in_subnet,
    ipv4_is_public,
    ipv6_in_subnet,
    ipv6_is_public,
    join_strings,
    less_than,
    less_than_or_equal,
    mappable,
    mod,
    mul,
    not_,
    now,
    or_,
    pow,
    prettify_json_str,
    regex_extract,
    regex_match,
    regex_not_match,
    serialize_to_json,
    slice_str,
    str_to_b64,
    sub,
    to_date_string,
    to_datetime,
    to_iso_format,
    to_timestamp_str,
    union,
    unique_items,
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
    original = "Hello World!"
    encoded = str_to_b64(original)
    assert encoded == "SGVsbG8gV29ybGQh"
    assert b64_to_str(encoded) == original


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


def test_dict_operations() -> None:
    test_dict = {"a": 1, "b": 2, "c": 3}
    assert set(dict_keys(test_dict)) == {"a", "b", "c"}
    assert set(dict_values(test_dict)) == {1, 2, 3}


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
    "a,b,expected",
    [
        (1, 2, True),
        (2, 2, False),
        (3, 2, False),
        ("a", "b", True),
        ("b", "a", False),
        (1.5, 2.5, True),
    ],
)
def test_less_than(a: Any, b: Any, expected: bool) -> None:
    assert less_than(a, b) == expected


@pytest.mark.parametrize(
    "a,b,expected",
    [
        (1, 2, True),
        (2, 2, True),
        (3, 2, False),
        ("a", "b", True),
        ("a", "a", True),
        (1.5, 2.5, True),
    ],
)
def test_less_than_or_equal(a: Any, b: Any, expected: bool) -> None:
    assert less_than_or_equal(a, b) == expected


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
