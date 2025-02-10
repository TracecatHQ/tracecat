from typing import Any

import pytest
from tracecat_registry.base.core.transform import (
    _build_safe_lambda,
    apply,
    deduplicate,
    difference,
    intersect,
)
from tracecat_registry.base.core.transform import (
    filter as filter_,
)


def test_build_lambda() -> None:
    add_one = _build_safe_lambda("lambda x: x + 1")
    assert add_one(1) == 2


def test_use_jsonpath_in_safe_lambda():
    data = {"name": "John"}
    jsonpath = _build_safe_lambda("lambda x: jsonpath('$.name', x) == 'John'")
    assert jsonpath(data) is True


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
    "items,python_lambda,expected",
    [
        ([1, 2, 3, 4, 5], "lambda x: x % 2 == 0", [2, 4]),
        (["a", "bb", "ccc"], "lambda x: len(x) > 1", ["bb", "ccc"]),
        (
            [{"value": 1}, {"value": 2}, {"value": 3}],
            "lambda x: x['value'] > 1",
            [{"value": 2}, {"value": 3}],
        ),
        ([1, 2, 3], "lambda x: x > 10", []),
        # Additional JSON/dictionary cases
        (
            [
                {"metrics": {"errors": 5}},
                {"metrics": {"errors": 0}},
                {"metrics": {"errors": 3}},
            ],
            "lambda x: x['metrics']['errors'] == 0",
            [{"metrics": {"errors": 0}}],
        ),
        (
            [
                {"user": {"active": True}},
                {"user": {"active": False}},
                {"user": {"active": True}},
            ],
            "lambda x: x['user']['active']",
            [{"user": {"active": True}}, {"user": {"active": True}}],
        ),
        (
            [
                {"config": {"enabled": True, "type": "A"}},
                {"config": {"enabled": True, "type": "B"}},
            ],
            "lambda x: x['config']['enabled'] and x['config']['type'] == 'A'",
            [{"config": {"enabled": True, "type": "A"}}],
        ),
    ],
)
def test_filter_(items: list[Any], python_lambda: str, expected: list[Any]) -> None:
    """Test the filter_ function with various conditions."""
    assert filter_(items, python_lambda) == expected


def test_filter_errors() -> None:
    """Test error cases for the filter_ function."""
    with pytest.raises(SyntaxError):
        filter_([1, 2, 3], "not a lambda")
    with pytest.raises(ValueError):
        filter_([1, 2, 3], "lambda x: import os")


@pytest.mark.parametrize(
    "input,python_lambda,expected",
    [
        # Test string format
        (["a", "b", "c"], "lambda x: f'field:{x}'", ["field:a", "field:b", "field:c"]),
        # Test arithmetic
        ([1, 2, 3], "lambda x: x + 1", [2, 3, 4]),
        # Test dict operations
        (
            [{"key": "a"}, {"key": "b"}, {"key": "c"}],
            "lambda x: x['key']",
            ["a", "b", "c"],
        ),
        # Test dictionary transformation
        (
            [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}],
            "lambda x: {'user': x['name'], 'uid': x['id']}",
            [{"user": "Alice", "uid": 1}, {"user": "Bob", "uid": 2}],
        ),
        # Test nested dictionary access
        (
            [
                {"user": {"id": 1, "info": {"age": 25}}},
                {"user": {"id": 2, "info": {"age": 30}}},
            ],
            "lambda x: x['user']['info']['age']",
            [25, 30],
        ),
        # Additional JSON/dictionary cases
        (
            [{"data": {"count": 5}}, {"data": {"count": 10}}, {"data": {"count": 15}}],
            "lambda x: x['data']['count'] > 10",
            [{"data": {"count": 15}}],
        ),
        (
            [{"tags": ["a", "b"]}, {"tags": ["c"]}, {"tags": ["a", "b", "c"]}],
            "lambda x: len(x['tags']) > 1",
            [{"tags": ["a", "b"]}, {"tags": ["a", "b", "c"]}],
        ),
        (
            [
                {"status": "active", "priority": 1},
                {"status": "inactive", "priority": 2},
            ],
            "lambda x: x['status'] == 'active' and x['priority'] > 0",
            [{"status": "active", "priority": 1}],
        ),
    ],
)
def test_apply(input: list[Any], python_lambda: str, expected: list[Any]) -> None:
    assert apply(input, python_lambda) == expected


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
        # Additional JSON/dictionary cases
        (
            [{"id": "a", "val": 1}, {"id": "b", "val": 2}],
            [{"id": "b", "val": 2}, {"id": "c", "val": 3}],
            "lambda x: x['id']",
            [{"id": "b", "val": 2}],
        ),
        (
            [{"settings": {"mode": "dark"}}, {"settings": {"mode": "light"}}],
            [{"settings": {"mode": "light"}}, {"settings": {"mode": "auto"}}],
            "lambda x: x['settings']['mode']",
            [{"settings": {"mode": "light"}}],
        ),
        (
            [{"meta": {"version": 1.0}}, {"meta": {"version": 2.0}}],
            [{"meta": {"version": 2.0}}, {"meta": {"version": 3.0}}],
            "lambda x: x['meta']['version']",
            [{"meta": {"version": 2.0}}],
        ),
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
    "items,collection,python_lambda,expected",
    [
        ([1, 2, 3], [2, 3, 4], [1]),  # Basic difference
        ([1, 2, 2], [2], [1]),  # Duplicates in first sequence
        ([], [1, 2], []),  # Empty first sequence
        ([1, 2], [], [1, 2]),  # Empty second sequence
        (["a", "b"], ["b", "c"], ["a"]),  # String elements
        # Dictionary transformation
        (
            [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}],
            [{"id": 2, "name": "Bob"}, {"id": 3, "name": "Charlie"}],
            [{"id": 1, "name": "Alice"}],
        ),
        # Additional JSON/dictionary cases
        (
            [{"id": "a", "val": 1}, {"id": "b", "val": 2}],
            [{"id": "b", "val": 2}, {"id": "c", "val": 3}],
            "lambda x: x['id']",
            [{"id": "b", "val": 2}],
        ),
        (
            [{"settings": {"mode": "dark"}}, {"settings": {"mode": "light"}}],
            [{"settings": {"mode": "light"}}, {"settings": {"mode": "auto"}}],
            "lambda x: x['settings']['mode']",
            [{"settings": {"mode": "light"}}],
        ),
        (
            [{"meta": {"version": 1.0}}, {"meta": {"version": 2.0}}],
            [{"meta": {"version": 2.0}}, {"meta": {"version": 3.0}}],
            "lambda x: x['meta']['version']",
            [{"meta": {"version": 2.0}}],
        ),
    ],
)
def test_difference(a: list[Any], b: list[Any], expected: list[Any]) -> None:
    """Test set difference between two sequences."""
    assert sorted(difference(a, b)) == sorted(expected)


@pytest.mark.parametrize(
    "items,python_lambda,expected",
    [
        ([1, 2, 3, 2, 1], None, [1, 2, 3]),
        # Test dictionary deduplication by specific key
        (
            [
                {"id": 1, "name": "Alice"},
                {"id": 2, "name": "Bob"},
                {"id": 1, "name": "Alice"},
            ],
            "lambda x: x['id']",
            [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}],
        ),
        # Test nested dictionary deduplication
        (
            [
                {"user": {"id": 1, "name": "Alice"}},
                {"user": {"id": 2, "name": "Bob"}},
                {"user": {"id": 1, "name": "Alice"}},
            ],
            "lambda x: x['user']['id']",
            [{"user": {"id": 1, "name": "Alice"}}, {"user": {"id": 2, "name": "Bob"}}],
        ),
    ],
)
def test_deduplicate(
    items: list[Any], python_lambda: str | None, expected: list[Any]
) -> None:
    assert deduplicate(items, python_lambda) == expected
