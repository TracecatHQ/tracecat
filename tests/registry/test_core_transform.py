from typing import Any

import pytest
from tracecat_registry.base.core.transform import (
    _build_safe_lambda,
    apply,
    deduplicate,
    is_in,
    is_not_in,
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
    "items,collection,python_lambda,unique,expected",
    [
        # Basic cases without unique
        ([1, 2, 3], [2, 3, 4], None, False, [2, 3]),
        # Test unique=True removes duplicates
        ([1, 2, 2, 3], [2, 2, 3, 4], None, True, [2, 3]),
        # Empty cases
        ([1, 2], [3, 4], None, False, []),
        ([], [1, 2], None, False, []),
        ([1, 2], [], None, False, []),
        # String values
        (["a", "b", "b"], ["b", "c"], None, False, ["b", "b"]),
        (["a", "b", "b"], ["b", "c"], None, True, ["b"]),
        # With lambda transformation, non-unique
        (
            [{"id": 1}, {"id": 2}, {"id": 2}],
            [2, 4, 6],
            "lambda x: x['id'] * 2",
            False,
            [{"id": 1}, {"id": 2}, {"id": 2}],
        ),
        # With lambda transformation, unique
        (
            [{"id": 1}, {"id": 2}, {"id": 2}],
            [2, 4, 6],
            "lambda x: x['id'] * 2",
            True,
            [{"id": 1}, {"id": 2}],
        ),
        # Complex objects with duplicates
        (
            [{"name": "a"}, {"name": "b"}, {"name": "b"}],
            [{"name": "b"}, {"name": "c"}],
            "lambda x: x['name']",
            False,
            [{"name": "b"}, {"name": "b"}],
        ),
        # Complex objects with unique=True
        (
            [{"name": "a"}, {"name": "b"}, {"name": "b"}],
            [{"name": "b"}, {"name": "c"}],
            "lambda x: x['name']",
            True,
            [{"name": "b"}],
        ),
    ],
)
def test_is_in(
    items: list,
    collection: list,
    python_lambda: str | None,
    unique: bool,
    expected: list,
) -> None:
    """Test the is_in function with various inputs and transformations."""
    result = is_in(items, collection, python_lambda, unique)
    # Sort the results to ensure consistent comparison
    assert sorted(result) == sorted(expected)


@pytest.mark.parametrize(
    "items,collection,python_lambda,unique,expected",
    [
        # Basic cases without unique
        ([1, 2, 3], [2, 3, 4], None, False, [1]),
        # Test unique=True removes duplicates
        ([1, 1, 2, 3], [2, 3, 4], None, True, [1]),
        # Empty cases
        ([], [1, 2], None, False, []),
        ([1, 2], [], None, False, [1, 2]),
        # String values with duplicates
        (["a", "a", "b"], ["b", "c"], None, False, ["a", "a"]),
        (["a", "a", "b"], ["b", "c"], None, True, ["a"]),
        # With lambda transformation, non-unique
        (
            [{"id": 1}, {"id": 1}, {"id": 2}],
            [{"id": 2}, {"id": 3}],
            "lambda x: x['id']",
            False,
            [{"id": 1}, {"id": 1}],
        ),
        # With lambda transformation, unique
        (
            [{"id": 1}, {"id": 1}, {"id": 2}],
            [{"id": 2}, {"id": 3}],
            "lambda x: x['id']",
            True,
            [{"id": 1}],
        ),
        # Complex nested objects
        (
            [{"user": {"id": 1}}, {"user": {"id": 1}}, {"user": {"id": 2}}],
            [{"user": {"id": 2}}],
            "lambda x: x['user']['id']",
            False,
            [{"user": {"id": 1}}, {"user": {"id": 1}}],
        ),
        # Complex nested objects with unique=True
        (
            [{"user": {"id": 1}}, {"user": {"id": 1}}, {"user": {"id": 2}}],
            [{"user": {"id": 2}}],
            "lambda x: x['user']['id']",
            True,
            [{"user": {"id": 1}}],
        ),
    ],
)
def test_is_not_in(
    items: list[Any],
    collection: list[Any],
    python_lambda: str | None,
    unique: bool,
    expected: list[Any],
) -> None:
    """Test filtering items not in the collection, with optional deduplication."""
    result = is_not_in(items, collection, python_lambda, unique)
    assert sorted(result) == sorted(expected)


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
