import pytest

from tracecat.expressions.functions import (
    deserialize_ndjson,
    extract_text_from_html,
    ipv4_in_subnet,
    ipv4_is_public,
    ipv6_in_subnet,
    ipv6_is_public,
    lambda_filter,
)


def test_lambda_filter_success():
    items = [1, 2, 3, 4, 5, 6, 7]

    expr = "x > 2"
    assert lambda_filter(items, expr) == [3, 4, 5, 6, 7]

    expr = "x > 2 and x < 6"
    assert lambda_filter(items, expr) == [3, 4, 5]

    expr = "x in [1,2,3]"
    assert lambda_filter(items, expr) == [1, 2, 3]

    listoflists = [[1, 2, 3], [4, 5, 6, 7, 8, 9], [10, 11], []]

    expr = "2 in x or 11 in x"
    assert lambda_filter(listoflists, expr) == [[1, 2, 3], [10, 11]]

    expr = "len(x) > 0"
    assert lambda_filter(listoflists, expr) == [[1, 2, 3], [4, 5, 6, 7, 8, 9], [10, 11]]

    expr = "len(x) < 3"
    assert lambda_filter(listoflists, expr) == [[10, 11], []]

    strlist = ["test@tracecat.com", "user@tracecat.com", "user@other.com"]
    expr = "'tracecat.com' in x"
    assert lambda_filter(strlist, expr) == ["test@tracecat.com", "user@tracecat.com"]


def test_lambda_filter_fails():
    items = [1, 2, 3, 4, 5, 6, 7]
    with pytest.raises(ValueError):
        lambda_filter(items, "sum(x)")

    listoflists = [[1, 2, 3], [4, 5, 6, 7, 8, 9], [10, 11], []]
    with pytest.raises(ValueError):
        lambda_filter(listoflists, "sum(x) > 1")

    with pytest.raises(ValueError):
        lambda_filter(items, "x + 1")
    with pytest.raises(ValueError):
        lambda_filter(items, "y + 1")
    with pytest.raises(ValueError):
        lambda_filter(items, "y > 1")

    with pytest.raises(ValueError):
        lambda_filter(items, "x > 2 and x < 6 and x + 1")

    with pytest.raises(ValueError):
        lambda_filter(items, "lambda x: x > 2")

    with pytest.raises(ValueError):
        lambda_filter(items, "eval()")

    with pytest.raises(ValueError):
        lambda_filter(items, "import os")


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
