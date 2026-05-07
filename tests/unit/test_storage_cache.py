"""Tests for SizedMemoryCache."""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

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
    async def test_ttl_expiry_syncs_tracking(self, monkeypatch: pytest.MonkeyPatch):
        """Test that TTL expiry syncs size tracking on get."""
        now = 100.0
        monkeypatch.setattr("tracecat.storage.utils.time.monotonic", lambda: now)
        cache = SizedMemoryCache(max_bytes=1024, ttl=0.1)

        await cache.set("key1", b"hello")
        assert cache.total_bytes == 5

        now = 100.11
        result = await cache.get("key1")
        assert result is None
        assert cache.total_bytes == 0
        assert cache.item_count == 0

    @pytest.mark.anyio
    async def test_set_purges_expired_entries_without_read(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Test that writes purge expired entries before capacity accounting."""
        now = 100.0
        monkeypatch.setattr("tracecat.storage.utils.time.monotonic", lambda: now)
        cache = SizedMemoryCache(max_bytes=1024, ttl=0.1)

        await cache.set("expired", b"hello")
        assert cache.total_bytes == 5

        now = 100.11
        await cache.set("fresh", b"world")

        assert cache.total_bytes == 5
        assert cache.item_count == 1
        assert await cache.get("fresh") == b"world"

    @pytest.mark.anyio
    async def test_set_purges_expired_mru_before_lru_eviction(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Test that expired MRU entries do not cause fresh LRU eviction."""
        now = 100.0
        monkeypatch.setattr("tracecat.storage.utils.time.monotonic", lambda: now)
        cache = SizedMemoryCache(max_bytes=100, ttl=0.2)

        await cache.set("expires_first", b"a" * 50)
        now = 100.05
        await cache.set("fresh_lru", b"b" * 40)
        assert await cache.get("expires_first") == b"a" * 50

        now = 100.21
        await cache.set("new", b"c" * 40)

        assert await cache.get("expires_first") is None
        assert await cache.get("fresh_lru") == b"b" * 40
        assert await cache.get("new") == b"c" * 40
        assert cache.total_bytes == 80

    @pytest.mark.anyio
    async def test_oversized_set_purges_expired_entries(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Test that rejected writes still purge expired entries."""
        now = 100.0
        monkeypatch.setattr("tracecat.storage.utils.time.monotonic", lambda: now)
        cache = SizedMemoryCache(max_bytes=100, ttl=0.1)

        await cache.set("expired", b"a" * 50)
        assert cache.total_bytes == 50

        now = 100.11
        await cache.set("too_big", b"b" * 101)

        assert cache.total_bytes == 0
        assert cache.item_count == 0
        assert await cache.get("too_big") is None

    @pytest.mark.anyio
    async def test_non_positive_ttl_disables_expiry(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Test that non-positive TTL preserves existing no-expiry behavior."""
        now = 100.0
        monkeypatch.setattr("tracecat.storage.utils.time.monotonic", lambda: now)
        cache = SizedMemoryCache(max_bytes=1024, ttl=0.0)

        await cache.set("key1", b"hello")

        now = 10_000.0
        assert await cache.get("key1") == b"hello"
        assert cache.total_bytes == 5
        assert cache.item_count == 1

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

    def test_concurrent_access_from_multiple_event_loops(self):
        """Test that one cache can be shared by separate event loops."""
        worker_count = 4
        iteration_count = 250
        cache = SizedMemoryCache(max_bytes=2048, ttl=300.0)
        barrier = threading.Barrier(worker_count)

        async def exercise_cache(worker_id: int) -> None:
            barrier.wait(timeout=5)
            for i in range(iteration_count):
                shared_key = f"shared-{i % 8}"
                worker_key = f"worker-{worker_id}-{i % 8}"

                await cache.set(shared_key, f"shared-value-{worker_id}-{i}".encode())
                await cache.get(shared_key)
                await cache.set(worker_key, f"worker-value-{worker_id}-{i}".encode())
                await cache.get(worker_key)
                await asyncio.sleep(0)

        def run_in_new_loop(worker_id: int) -> None:
            asyncio.run(exercise_cache(worker_id))

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [
                executor.submit(run_in_new_loop, worker_id)
                for worker_id in range(worker_count)
            ]
            for future in futures:
                future.result()

        assert cache.total_bytes <= 2048

    def test_shared_cache_handles_cross_loop_lock_contention(self):
        """Test that cross-loop cache users can contend on the shared lock."""
        worker_count = 3
        cache = SizedMemoryCache(max_bytes=2048, ttl=300.0)
        lock_acquired = threading.Event()
        release_lock = threading.Event()
        start_barrier = threading.Barrier(worker_count + 1)
        workers_attempting_cache_access = threading.Semaphore(0)

        def hold_cache_lock() -> None:
            # This intentionally touches the lock internals: the regression is
            # reintroducing an event-loop-bound lock on this process-global cache.
            with cache._lock:
                lock_acquired.set()
                assert release_lock.wait(timeout=5)

        async def exercise_cache(worker_id: int) -> None:
            start_barrier.wait(timeout=5)
            workers_attempting_cache_access.release()

            key = f"key-{worker_id}"
            value = f"value-{worker_id}".encode()
            await cache.set(key, value)
            assert await cache.get(key) == value

        def run_in_new_loop(worker_id: int) -> None:
            asyncio.run(exercise_cache(worker_id))

        executor = ThreadPoolExecutor(max_workers=worker_count + 1)
        try:
            holder_future = executor.submit(hold_cache_lock)
            if not lock_acquired.wait(timeout=5):
                if holder_future.done():
                    holder_future.result()
                pytest.fail("Cache lock was not acquired")

            futures = [
                executor.submit(run_in_new_loop, worker_id)
                for worker_id in range(worker_count)
            ]
            start_barrier.wait(timeout=5)
            for _ in range(worker_count):
                assert workers_attempting_cache_access.acquire(timeout=5)

            release_lock.set()
            for future in futures:
                future.result(timeout=5)
            holder_future.result(timeout=5)
        finally:
            release_lock.set()
            executor.shutdown(wait=False, cancel_futures=True)

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
