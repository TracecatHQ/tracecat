"""Tests for SizedMemoryCache."""

import asyncio

import pytest

from tracecat.storage.utils import SizedMemoryCache


class TestSizedMemoryCache:
    """Test byte-aware memory cache with LRU eviction."""

    @pytest.mark.anyio
    async def test_get_set_basic(self):
        """Test basic get/set operations."""
        cache = SizedMemoryCache(max_bytes=1024, ttl=300.0)

        await cache.set("key1", b"hello")
        result = await cache.get("key1")

        assert result == b"hello"
        assert cache.total_bytes == 5
        assert cache.item_count == 1

    @pytest.mark.anyio
    async def test_get_missing_key_returns_none(self):
        """Test that get returns None for missing keys."""
        cache = SizedMemoryCache(max_bytes=1024, ttl=300.0)

        result = await cache.get("nonexistent")

        assert result is None

    @pytest.mark.anyio
    async def test_set_overwrites_existing_key(self):
        """Test that setting an existing key updates the value and size tracking."""
        cache = SizedMemoryCache(max_bytes=1024, ttl=300.0)

        await cache.set("key1", b"hello")  # 5 bytes
        assert cache.total_bytes == 5

        await cache.set("key1", b"hello world")  # 11 bytes
        assert cache.total_bytes == 11
        assert cache.item_count == 1

        result = await cache.get("key1")
        assert result == b"hello world"

    @pytest.mark.anyio
    async def test_lru_eviction_when_over_budget(self):
        """Test that LRU items are evicted when cache exceeds max_bytes."""
        cache = SizedMemoryCache(max_bytes=100, ttl=300.0)

        # Add items totaling 60 bytes
        await cache.set("key1", b"a" * 20)
        await cache.set("key2", b"b" * 20)
        await cache.set("key3", b"c" * 20)

        assert cache.total_bytes == 60
        assert cache.item_count == 3

        # Add item that pushes over budget - should evict key1 (LRU)
        await cache.set("key4", b"d" * 50)

        assert cache.total_bytes <= 100
        assert await cache.get("key1") is None  # Evicted
        assert await cache.get("key4") == b"d" * 50  # New item present

    @pytest.mark.anyio
    async def test_lru_eviction_evicts_multiple_items(self):
        """Test that multiple LRU items are evicted if needed."""
        cache = SizedMemoryCache(max_bytes=100, ttl=300.0)

        # Add 5 items of 20 bytes each = 100 bytes total
        for i in range(5):
            await cache.set(f"key{i}", b"x" * 20)

        assert cache.total_bytes == 100
        assert cache.item_count == 5

        # Add 60 byte item - must evict at least 3 items
        await cache.set("big", b"y" * 60)

        assert cache.total_bytes <= 100
        # At least key0, key1, key2 should be evicted
        assert await cache.get("key0") is None
        assert await cache.get("key1") is None
        assert await cache.get("key2") is None
        assert await cache.get("big") == b"y" * 60

    @pytest.mark.anyio
    async def test_lru_order_updated_on_get(self):
        """Test that accessing an item moves it to end of LRU order."""
        cache = SizedMemoryCache(max_bytes=100, ttl=300.0)

        await cache.set("key1", b"a" * 30)
        await cache.set("key2", b"b" * 30)
        await cache.set("key3", b"c" * 30)

        # Access key1 to make it recently used
        await cache.get("key1")

        # Add item that requires eviction - key2 should be evicted (now LRU)
        await cache.set("key4", b"d" * 50)

        assert await cache.get("key1") is not None  # Still present (was accessed)
        assert await cache.get("key2") is None  # Evicted (was LRU)

    @pytest.mark.anyio
    async def test_ttl_expiry_syncs_tracking(self):
        """Test that TTL expiry syncs size tracking on get."""
        cache = SizedMemoryCache(max_bytes=1024, ttl=0.1)  # 100ms TTL

        await cache.set("key1", b"hello")
        assert cache.total_bytes == 5

        # Wait for TTL to expire
        await asyncio.sleep(0.15)

        # Get should return None and sync tracking
        result = await cache.get("key1")
        assert result is None
        assert cache.total_bytes == 0
        assert cache.item_count == 0

    @pytest.mark.anyio
    async def test_concurrent_access(self):
        """Test that concurrent access is safe."""
        cache = SizedMemoryCache(max_bytes=10000, ttl=300.0)

        async def writer(key: str, value: bytes):
            for _ in range(10):
                await cache.set(key, value)
                await asyncio.sleep(0.001)

        async def reader(key: str):
            for _ in range(10):
                await cache.get(key)
                await asyncio.sleep(0.001)

        # Run concurrent readers and writers
        tasks = [
            writer("key1", b"value1"),
            writer("key2", b"value2"),
            reader("key1"),
            reader("key2"),
        ]
        await asyncio.gather(*tasks)

        # Should complete without errors
        assert cache.item_count <= 2

    @pytest.mark.anyio
    async def test_empty_value(self):
        """Test caching empty bytes."""
        cache = SizedMemoryCache(max_bytes=1024, ttl=300.0)

        await cache.set("empty", b"")
        result = await cache.get("empty")

        assert result == b""
        assert cache.total_bytes == 0
        assert cache.item_count == 1

    @pytest.mark.anyio
    async def test_single_item_exceeds_budget(self):
        """Test adding single item that fills entire budget."""
        cache = SizedMemoryCache(max_bytes=100, ttl=300.0)

        await cache.set("key1", b"a" * 50)
        await cache.set("key2", b"b" * 100)  # Fills entire budget

        assert cache.total_bytes == 100
        assert await cache.get("key1") is None  # Evicted
        assert await cache.get("key2") == b"b" * 100

    @pytest.mark.anyio
    async def test_properties(self):
        """Test total_bytes and item_count properties."""
        cache = SizedMemoryCache(max_bytes=1024, ttl=300.0)

        assert cache.total_bytes == 0
        assert cache.item_count == 0

        await cache.set("a", b"123")
        await cache.set("b", b"4567")

        assert cache.total_bytes == 7
        assert cache.item_count == 2
