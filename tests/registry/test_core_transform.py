import asyncio
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

from tracecat.types.exceptions import TracecatExpressionError


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
        # Dict input case
        (
            {"id": 1, "name": "Alice"},
            ["id"],
            [{"id": 1, "name": "Alice"}],
        ),
        # Dict input case with multiple keys
        (
            {"id": 1, "name": "Alice"},
            ["id", "name"],
            [{"id": 1, "name": "Alice"}],
        ),
    ],
)
@pytest.mark.anyio
async def test_deduplicate(
    items: list[dict[str, Any]], keys: list[str], expected: list[dict[str, Any]]
) -> None:
    """Test the deduplicate function with various inputs and transformations."""
    try:
        result = await deduplicate(items, keys)
        assert result == expected
    except ConnectionError:
        pytest.skip("Redis not available")


@pytest.mark.parametrize(
    "first_call,second_call,expected_first,expected_second",
    [
        # Basic persistence test
        (
            [{"id": 99}, {"id": 100}],
            [{"id": 99}, {"id": 100}],
            [{"id": 99}, {"id": 100}],
            [],
        ),
        # Partial overlap
        (
            [{"id": 1}, {"id": 2}],
            [{"id": 2}, {"id": 3}],
            [{"id": 1}, {"id": 2}],
            [{"id": 3}],
        ),
        # Different fields, same keys
        (
            [{"id": 1, "name": "Alice"}],
            [{"id": 1, "name": "Bob", "age": 30}],
            [{"id": 1, "name": "Alice"}],
            [],
        ),
        # Empty second call
        (
            [{"id": 1}],
            [],
            [{"id": 1}],
            [],
        ),
        # Empty first call
        (
            [],
            [{"id": 1}],
            [],
            [{"id": 1}],
        ),
        # Dict input case deduplication
        (
            {"id": 1, "name": "Alice"},
            {"id": 1, "name": "Alice"},
            [{"id": 1, "name": "Alice"}],
            [],
        ),
        # Dict input case no deduplication
        (
            {"id": 1, "name": "Alice"},
            {"id": 2, "name": "Alice"},
            [{"id": 1, "name": "Alice"}],
            [{"id": 2, "name": "Alice"}],
        ),
    ],
)
@pytest.mark.anyio
async def test_deduplicate_persistence(
    first_call: list[dict[str, Any]],
    second_call: list[dict[str, Any]],
    expected_first: list[dict[str, Any]],
    expected_second: list[dict[str, Any]],
) -> None:
    """Test that deduplication persists across multiple calls."""
    try:
        first_result = await deduplicate(first_call, ["id"])
        assert first_result == expected_first

        second_result = await deduplicate(second_call, ["id"])
        assert second_result == expected_second
    except ConnectionError:
        pytest.skip("Redis not available")


@pytest.mark.parametrize(
    "items,keys,description",
    [
        # Special values in keys
        (
            [{"id": None}, {"id": None}],
            ["id"],
            "None values",
        ),
        (
            [{"id": ""}, {"id": ""}],
            ["id"],
            "Empty strings",
        ),
        (
            [{"id": 0}, {"id": 0}],
            ["id"],
            "Zero values",
        ),
        (
            [{"id": False}, {"id": False}],
            ["id"],
            "Boolean False",
        ),
        # Complex key types
        (
            [{"data": {"nested": [1, 2, 3]}}, {"data": {"nested": [1, 2, 3]}}],
            ["data.nested"],
            "Array values as keys",
        ),
        (
            [{"config": {"settings": {"a": 1}}}, {"config": {"settings": {"a": 1}}}],
            ["config.settings"],
            "Dict values as keys",
        ),
        # Unicode and special characters
        (
            [{"name": "José"}, {"name": "José"}],
            ["name"],
            "Unicode characters",
        ),
        (
            [{"path": "/usr/bin/test"}, {"path": "/usr/bin/test"}],
            ["path"],
            "Path with slashes",
        ),
        (
            [{"email": "test@example.com"}, {"email": "test@example.com"}],
            ["email"],
            "Email addresses",
        ),
    ],
)
@pytest.mark.anyio
async def test_deduplicate_special_values(
    items: list[dict[str, Any]],
    keys: list[str],
    description: str,
) -> None:
    """Test deduplication with special values and edge cases."""
    try:
        # First call should return the first item only (deduped within call)
        result = await deduplicate(items, keys)
        assert len(result) == 1, f"Failed for {description}"

        # Second call should return empty (persisted dedup)
        result2 = await deduplicate(items, keys)
        assert result2 == [], f"Failed persistence for {description}"
    except ConnectionError:
        pytest.skip("Redis not available")


@pytest.mark.parametrize(
    "input_data,keys,expected_type",
    [
        # Single dict input/output
        (
            {"id": 1, "name": "test"},
            ["id"],
            list,
        ),
        # List with single item returns list
        (
            [{"id": 1, "name": "test"}],
            ["id"],
            list,
        ),
        # Empty list returns list
        (
            [],
            ["id"],
            list,
        ),
    ],
)
@pytest.mark.anyio
async def test_deduplicate_return_types(
    input_data: dict[str, Any] | list[dict[str, Any]],
    keys: list[str],
    expected_type: type,
) -> None:
    """Test that deduplicate returns the correct type based on input."""
    try:
        result = await deduplicate(input_data, keys)
        assert isinstance(result, expected_type)

        # For dict input, second call should return empty list
        if isinstance(input_data, dict):
            result2 = await deduplicate(input_data, keys)
            assert result2 == []
    except ConnectionError:
        pytest.skip("Redis not available")


@pytest.mark.anyio
async def test_deduplicate_ttl_expiry() -> None:
    """Test that items are no longer considered duplicates after TTL expires."""
    try:
        payload = [{"id": 200}]

        # First call with 1 second TTL
        first = await deduplicate(payload, ["id"], expire_seconds=1)
        assert first == payload

        # Second call immediately should be filtered
        second = await deduplicate(payload, ["id"], expire_seconds=1)
        assert second == []

        # Wait for TTL to expire
        await asyncio.sleep(1.1)

        # Third call should work again
        third = await deduplicate(payload, ["id"], expire_seconds=1)
        assert third == payload
    except ConnectionError:
        pytest.skip("Redis not available")


@pytest.mark.parametrize(
    "items,keys,error_type",
    [
        # Missing keys
        (
            [{"id": 1}, {"name": "test"}],
            ["id"],
            TracecatExpressionError,
        ),
        # Invalid jsonpath
        (
            [{"id": 1}],
            ["id..invalid"],
            TracecatExpressionError,
        ),
        # Non-dict items in list
        (
            ["not a dict"],
            ["id"],
            TracecatExpressionError,
        ),
    ],
)
@pytest.mark.anyio
async def test_deduplicate_error_cases(
    items: Any,
    keys: list[str],
    error_type: type[Exception],
) -> None:
    """Test that deduplicate handles error cases appropriately."""
    with pytest.raises(error_type):
        await deduplicate(items, keys)


@pytest.mark.anyio
async def test_deduplicate_concurrent_calls() -> None:
    """Test that concurrent calls to deduplicate work correctly."""
    try:
        # Create multiple items that will be processed concurrently
        items = [{"id": i, "data": f"item_{i}"} for i in range(10)]

        # Run multiple concurrent deduplicate calls
        async def dedupe_task(item_subset):
            return await deduplicate(item_subset, ["id"])

        # Split items into overlapping subsets
        subset1 = items[:6]  # items 0-5
        subset2 = items[4:8]  # items 4-7 (overlap with subset1)
        subset3 = items[6:]  # items 6-9 (overlap with subset2)

        # Run concurrently
        results = await asyncio.gather(
            dedupe_task(subset1),
            dedupe_task(subset2),
            dedupe_task(subset3),
        )

        # Collect all returned items
        all_results = []
        for result in results:
            all_results.extend(result)

        # Should have no duplicates across all results
        seen_ids = set()
        for item in all_results:
            assert item["id"] not in seen_ids, f"Duplicate id {item['id']} found"
            seen_ids.add(item["id"])

        # Should have all 10 unique items
        assert len(seen_ids) == 10
    except ConnectionError:
        pytest.skip("Redis not available")


@pytest.mark.parametrize(
    "keys,description",
    [
        # Multiple levels of nesting
        (
            ["data.user.profile.settings.id"],
            "Deep nesting (5 levels)",
        ),
        # Multiple keys with different depths
        (
            ["type", "data.id", "meta.source.system"],
            "Mixed depth keys",
        ),
        # Many keys
        (
            [f"field{i}" for i in range(10)],
            "Many keys (10)",
        ),
    ],
)
@pytest.mark.anyio
async def test_deduplicate_complex_keys(
    keys: list[str],
    description: str,
) -> None:
    """Test deduplication with complex key configurations."""
    try:
        # Build test data based on keys
        def build_nested_dict(path: str, value: Any) -> dict:
            parts = path.split(".")
            result = {}
            current = result
            for part in parts[:-1]:
                current[part] = {}
                current = current[part]
            current[parts[-1]] = value
            return result

        # Create two items with same key values
        item1 = {}
        item2 = {}
        for i, key in enumerate(keys):
            nested1 = build_nested_dict(key, f"value_{i}")
            nested2 = build_nested_dict(key, f"value_{i}")

            # Merge nested dicts
            def deep_merge(d1, d2):
                for k, v in d2.items():
                    if k in d1 and isinstance(d1[k], dict) and isinstance(v, dict):
                        deep_merge(d1[k], v)
                    else:
                        d1[k] = v

            deep_merge(item1, nested1)
            deep_merge(item2, nested2)

        items = [item1, item2]

        # Should deduplicate to one item
        result = await deduplicate(items, keys)
        assert len(result) == 1, f"Failed for {description}"
    except ConnectionError:
        pytest.skip("Redis not available")


@pytest.mark.anyio
async def test_deduplicate_redis_operation_error(monkeypatch) -> None:
    """Test that deduplicate raises ConnectionError on Redis operation failures."""

    # Mock redis.from_url to return a failing client
    class MockRedisClient:
        async def set(self, *args, **kwargs):
            raise Exception("Redis SET failed")

        async def aclose(self):
            pass

        def pipeline(self, *args, **kwargs):
            return self

    def mock_from_url(*args, **kwargs):
        return MockRedisClient()

    # Import redis.asyncio within the function to patch it
    import redis.asyncio as redis

    monkeypatch.setattr(redis, "from_url", mock_from_url)

    with pytest.raises(ConnectionError, match="key-value store.*"):
        await deduplicate([{"id": 1}], ["id"])


@pytest.mark.anyio
async def test_deduplicate_skip_persistence_vs_redis() -> None:
    """Test that persist=True persists across calls, but persist=False doesn't."""
    items_persist = [{"id": 998, "data": "test_persist"}]
    items_no_persist = [{"id": 997, "data": "test_no_persist"}]
    keys = ["id"]

    try:
        # First call with persist=True should return the item
        result1 = await deduplicate(items_persist, keys, persist=True)
        assert result1 == items_persist

        # Second call with persist=True should return empty (Redis persistence)
        result2 = await deduplicate(items_persist, keys, persist=True)
        assert result2 == []

        # First call with persist=False should return the item
        result3 = await deduplicate(items_no_persist, keys, persist=False)
        assert result3 == items_no_persist

        # Second call with persist=False should also return the item (no persistence)
        result4 = await deduplicate(items_no_persist, keys, persist=False)
        assert result4 == items_no_persist

    except ConnectionError:
        pytest.skip("Redis not available")
