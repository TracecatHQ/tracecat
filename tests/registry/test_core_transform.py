from typing import Any

import pytest
from tracecat_registry.core.transform import (
    apply,
    deduplicate,
    filter,
    is_in,
    map,
    not_in,
)


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
def test_filter(items: list[Any], python_lambda: str, expected: list[Any]) -> None:
    """Test the filter_ function with various conditions."""
    assert filter(items, python_lambda) == expected


@pytest.mark.parametrize(
    "input,python_lambda,expected",
    [
        # Basic transformations
        (1, "lambda x: x + 1", 2),
        ("hello", "lambda x: x.upper()", "HELLO"),
        # JSON/dict transformations
        (
            {"ip": "192.168.1.1", "severity": "low"},
            "lambda x: {**x, 'severity': x['severity'].upper()}",
            {"ip": "192.168.1.1", "severity": "LOW"},
        ),
        # String formatting for alerts
        (
            "suspicious_login",
            'lambda x: f\'ALERT: {x.replace("_", " ").title()}\'',
            "ALERT: Suspicious Login",
        ),
        # Timestamp conversions
        (
            "2024-03-14T12:00:00Z",
            "lambda x: x.replace('T', ' ').replace('Z', ' UTC')",
            "2024-03-14 12:00:00 UTC",
        ),
        # Risk score normalization
        (75, "lambda x: 'High' if x >= 70 else 'Medium' if x >= 40 else 'Low'", "High"),
        # Hash formatting
        (
            "5e884898da28047151d0e56f8dc6292773603d0d6aabbdd62a11ef721d1542d8",
            "lambda x: x[:8] + '...' + x[-8:]",
            "5e884898...1d1542d8",
        ),
        # Network data transformation
        (
            {"source_ip": "10.0.0.1", "dest_port": "443"},
            "lambda x: f\"{x['source_ip']}:{x['dest_port']}\"",
            "10.0.0.1:443",
        ),
    ],
)
def test_apply(input: Any, python_lambda: str, expected: Any) -> None:
    assert apply(input, python_lambda) == expected


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
        # Test filter conditions
        (
            [{"data": {"count": 5}}, {"data": {"count": 10}}, {"data": {"count": 15}}],
            "lambda x: x['data']['count'] > 10",
            [False, False, True],
        ),
        (
            [{"tags": ["a", "b"]}, {"tags": ["c"]}, {"tags": ["a", "b", "c"]}],
            "lambda x: len(x['tags']) > 1",
            [True, False, True],
        ),
        (
            [
                {"status": "active", "priority": 1},
                {"status": "inactive", "priority": 2},
            ],
            "lambda x: x['status'] == 'active' and x['priority'] > 0",
            [True, False],
        ),
    ],
)
def test_map(input: list[Any], python_lambda: str, expected: list[Any]) -> None:
    assert map(input, python_lambda) == expected


@pytest.mark.parametrize(
    "items,collection,python_lambda,expected",
    [
        # Basic cases
        ([1, 2, 3], [2, 3, 4], None, [2, 3]),
        # Empty cases
        ([1, 2], [3, 4], None, []),
        ([], [1, 2], None, []),
        ([1, 2], [], None, []),
        # String values
        (["a", "b", "b"], ["b", "c"], None, ["b", "b"]),
        # With lambda transformation
        (
            [{"id": 1}, {"id": 2}, {"id": 2}],
            [2, 4, 6],
            "lambda x: x['id'] * 2",
            [{"id": 1}, {"id": 2}, {"id": 2}],
        ),
        # Complex objects with duplicates - using hashable collection
        (
            [{"name": "a"}, {"name": "b"}, {"name": "b"}],
            ["b", "c"],  # Collection contains transformed values
            "lambda x: x['name']",
            [{"name": "b"}, {"name": "b"}],
        ),
    ],
)
def test_is_in(
    items: list,
    collection: list,
    python_lambda: str | None,
    expected: list,
) -> None:
    """Test the is_in function with various inputs and transformations."""
    result = is_in(items, collection, python_lambda)
    assert result == expected


@pytest.mark.parametrize(
    "items,collection,python_lambda,expected",
    [
        # Basic cases
        ([1, 2, 3], [2, 3, 4], None, [1]),
        # Empty cases
        ([], [1, 2], None, []),
        ([1, 2], [], None, [1, 2]),
        # String values with duplicates
        (["a", "a", "b"], ["b", "c"], None, ["a", "a"]),
        # With lambda transformation
        (
            [{"id": 1}, {"id": 1}, {"id": 2}],
            [2, 3],  # Collection contains transformed values
            "lambda x: x['id']",
            [{"id": 1}, {"id": 1}],
        ),
        # Complex nested objects
        (
            [{"user": {"id": 1}}, {"user": {"id": 1}}, {"user": {"id": 2}}],
            [2],  # Collection contains transformed values
            "lambda x: x['user']['id']",
            [{"user": {"id": 1}}, {"user": {"id": 1}}],
        ),
    ],
)
def test_not_in(
    items: list[Any],
    collection: list[Any],
    python_lambda: str | None,
    expected: list[Any],
) -> None:
    """Test filtering items not in the collection."""
    result = not_in(items, collection, python_lambda)
    assert result == expected


@pytest.mark.parametrize(
    "items,keys,expected",
    [
        # Dictionary deduplication by single key
        (
            [
                {"id": 1, "name": "Alice"},
                {"id": 2, "name": "Bob"},
                {"id": 1, "name": "Alice2"},  # Should merge with first entry
            ],
            ["id"],
            [{"id": 1, "name": "Alice2"}, {"id": 2, "name": "Bob"}],
        ),
        # Nested dictionary deduplication
        (
            [
                {"user": {"id": 1, "name": "Alice"}},
                {"user": {"id": 2, "name": "Bob"}},
                {"user": {"id": 1, "name": "Alice2"}},
            ],
            ["user.id"],
            [
                {"user": {"id": 1, "name": "Alice2"}},
                {"user": {"id": 2, "name": "Bob"}},
            ],
        ),
        # Multiple key deduplication
        (
            [
                {"type": "user", "id": 1, "data": "old"},
                {"type": "user", "id": 2, "data": "test"},
                {"type": "user", "id": 1, "data": "new"},
            ],
            ["type", "id"],
            [
                {"type": "user", "id": 1, "data": "new"},
                {"type": "user", "id": 2, "data": "test"},
            ],
        ),
        # Deep nested keys with multiple levels
        (
            [
                {"data": {"user": {"profile": {"id": 1, "info": "old"}}}},
                {"data": {"user": {"profile": {"id": 2, "info": "test"}}}},
                {"data": {"user": {"profile": {"id": 1, "info": "new"}}}},
            ],
            ["data.user.profile.id"],
            [
                {"data": {"user": {"profile": {"id": 1, "info": "new"}}}},
                {"data": {"user": {"profile": {"id": 2, "info": "test"}}}},
            ],
        ),
        # Multiple nested keys combination
        (
            [
                {"meta": {"type": "event", "source": "app1"}, "data": {"id": 1}},
                {"meta": {"type": "event", "source": "app2"}, "data": {"id": 2}},
                {"meta": {"type": "event", "source": "app1"}, "data": {"id": 1}},
            ],
            ["meta.source", "data.id"],
            [
                {"meta": {"type": "event", "source": "app1"}, "data": {"id": 1}},
                {"meta": {"type": "event", "source": "app2"}, "data": {"id": 2}},
            ],
        ),
        # Array within nested structure
        (
            [
                {"user": {"id": 1, "tags": ["a", "b"], "status": "active"}},
                {"user": {"id": 2, "tags": ["c"], "status": "inactive"}},
                {"user": {"id": 1, "tags": ["d"], "status": "pending"}},
            ],
            ["user.id"],
            [
                {"user": {"id": 1, "tags": ["d"], "status": "pending"}},
                {"user": {"id": 2, "tags": ["c"], "status": "inactive"}},
            ],
        ),
        # Empty list case
        ([], ["id"], []),
        # Single item case
        (
            [{"deep": {"nested": {"id": 1, "value": "test"}}}],
            ["deep.nested.id"],
            [{"deep": {"nested": {"id": 1, "value": "test"}}}],
        ),
    ],
)
def test_deduplicate(
    items: list[dict[str, Any]], keys: list[str], expected: list[dict[str, Any]]
) -> None:
    assert deduplicate(items, keys) == expected
