"""Tests for the object storage module.

Uses InMemoryObjectStorage as the test double - no mocks needed.
"""

import pytest

from tracecat.expressions.common import ExprContext
from tracecat.storage.object import (
    HydrationCache,
    InMemoryObjectStorage,
    ObjectRef,
    StoredObject,
    compute_sha256,
    deserialize_object,
    get_object_storage,
    hydrate_execution_context,
    hydrate_value,
    is_object_ref,
    reset_object_storage,
    serialize_object,
    set_object_storage,
    to_object_ref,
)


class TestObjectRef:
    """Tests for ObjectRef model."""

    def test_create_object_ref(self):
        """Test creating an ObjectRef with required fields."""
        ref = ObjectRef(
            bucket="test-bucket",
            key="results/test.json",
            size_bytes=1024,
            sha256="abc123",
        )
        assert ref.backend == "s3"
        assert ref.bucket == "test-bucket"
        assert ref.key == "results/test.json"
        assert ref.size_bytes == 1024
        assert ref.sha256 == "abc123"
        assert ref.content_type == "application/json"
        assert ref.encoding == "json"
        assert ref.kind is None

    def test_object_ref_with_kind(self):
        """Test ObjectRef with optional kind field."""
        ref = ObjectRef(
            bucket="bucket",
            key="key",
            size_bytes=100,
            sha256="hash",
            kind="action_result",
        )
        assert ref.kind == "action_result"

    def test_object_ref_serialization_roundtrip(self):
        """Test ObjectRef can be serialized and deserialized."""
        ref = ObjectRef(
            bucket="bucket",
            key="key",
            size_bytes=100,
            sha256="deadbeef",
            kind="trigger",
        )
        data = ref.model_dump()
        restored = ObjectRef.model_validate(data)

        assert restored.bucket == ref.bucket
        assert restored.key == ref.key
        assert restored.sha256 == ref.sha256
        assert restored.kind == ref.kind


class TestStoredObject:
    """Tests for StoredObject dataclass."""

    def test_inline_stored_object(self):
        """Test StoredObject for inline data."""
        stored = StoredObject(data={"foo": "bar"}, ref=None)
        assert stored.data == {"foo": "bar"}
        assert stored.ref is None
        assert not stored.is_externalized

    def test_externalized_stored_object(self):
        """Test StoredObject for externalized data."""
        ref = ObjectRef(
            bucket="bucket",
            key="key",
            size_bytes=1000,
            sha256="hash",
        )
        stored = StoredObject(data=None, ref=ref)
        assert stored.data is None
        assert stored.ref is ref
        assert stored.is_externalized


class TestInMemoryObjectStorage:
    """Tests for InMemoryObjectStorage."""

    @pytest.mark.anyio
    async def test_store_always_inline(self):
        """InMemoryObjectStorage always returns inline data."""
        storage = InMemoryObjectStorage()
        data = {"large": "x" * 1_000_000}  # 1MB data

        stored = await storage.store("key", data)

        assert stored.data == data
        assert stored.ref is None
        assert not stored.is_externalized

    @pytest.mark.anyio
    async def test_retrieve_raises_not_implemented(self):
        """InMemoryObjectStorage.retrieve raises NotImplementedError."""
        storage = InMemoryObjectStorage()
        ref = ObjectRef(
            bucket="bucket",
            key="key",
            size_bytes=100,
            sha256="hash",
        )

        with pytest.raises(NotImplementedError):
            await storage.retrieve(ref)

    @pytest.mark.anyio
    async def test_store_with_kind(self):
        """Test store with kind parameter."""
        storage = InMemoryObjectStorage()
        stored = await storage.store("key", {"data": 1}, kind="action_result")

        assert stored.data == {"data": 1}
        assert not stored.is_externalized


class TestSerializationHelpers:
    """Tests for serialization helpers."""

    def test_serialize_object(self):
        """Test JSON serialization."""
        data = {"foo": "bar", "num": 42}
        serialized = serialize_object(data)

        assert isinstance(serialized, bytes)
        assert b"foo" in serialized
        assert b"bar" in serialized

    def test_deserialize_object(self):
        """Test JSON deserialization."""
        content = b'{"foo":"bar","num":42}'
        data = deserialize_object(content)

        assert data == {"foo": "bar", "num": 42}

    def test_serialize_deserialize_roundtrip(self):
        """Test serialization roundtrip."""
        original = {"nested": {"list": [1, 2, 3]}, "string": "hello"}
        serialized = serialize_object(original)
        restored = deserialize_object(serialized)

        assert restored == original

    def test_compute_sha256(self):
        """Test SHA256 computation."""
        content = b"test content"
        hash1 = compute_sha256(content)
        hash2 = compute_sha256(content)

        assert hash1 == hash2  # Same content = same hash
        assert len(hash1) == 64  # Hex SHA256 is 64 chars
        assert compute_sha256(b"different") != hash1


class TestIsObjectRef:
    """Tests for is_object_ref detection."""

    def test_detects_object_ref_instance(self):
        """Test detection of ObjectRef instance."""
        ref = ObjectRef(
            bucket="bucket",
            key="key",
            size_bytes=100,
            sha256="hash",
        )
        assert is_object_ref(ref)

    def test_detects_object_ref_dict(self):
        """Test detection of dict with ObjectRef structure."""
        ref_dict = {
            "backend": "s3",
            "bucket": "bucket",
            "key": "key",
            "sha256": "hash",
            "size_bytes": 100,
        }
        assert is_object_ref(ref_dict)

    def test_rejects_regular_dict(self):
        """Test rejection of regular dict."""
        regular = {"foo": "bar"}
        assert not is_object_ref(regular)

    def test_rejects_partial_object_ref(self):
        """Test rejection of dict missing required fields."""
        partial = {
            "backend": "s3",
            "bucket": "bucket",
            # missing key and sha256
        }
        assert not is_object_ref(partial)

    def test_rejects_wrong_backend(self):
        """Test rejection of dict with wrong backend."""
        wrong_backend = {
            "backend": "azure",  # Not "s3"
            "bucket": "bucket",
            "key": "key",
            "sha256": "hash",
        }
        assert not is_object_ref(wrong_backend)

    def test_rejects_primitives(self):
        """Test rejection of primitive values."""
        assert not is_object_ref("string")
        assert not is_object_ref(42)
        assert not is_object_ref(None)
        assert not is_object_ref([1, 2, 3])


class TestToObjectRef:
    """Tests for to_object_ref conversion."""

    def test_passthrough_object_ref(self):
        """Test that ObjectRef passes through."""
        ref = ObjectRef(
            bucket="bucket",
            key="key",
            size_bytes=100,
            sha256="hash",
        )
        result = to_object_ref(ref)
        assert result is ref

    def test_convert_dict_to_object_ref(self):
        """Test conversion of dict to ObjectRef."""
        ref_dict = {
            "backend": "s3",
            "bucket": "bucket",
            "key": "key",
            "sha256": "hash",
            "size_bytes": 100,
        }
        result = to_object_ref(ref_dict)

        assert isinstance(result, ObjectRef)
        assert result.bucket == "bucket"
        assert result.key == "key"

    def test_raises_on_invalid_value(self):
        """Test ValueError on invalid value."""
        with pytest.raises(ValueError):
            to_object_ref({"invalid": "dict"})


class TestHydrationCache:
    """Tests for HydrationCache."""

    def test_cache_miss_returns_none(self):
        """Test cache miss returns None."""
        cache = HydrationCache()
        ref = ObjectRef(
            bucket="bucket",
            key="key",
            size_bytes=100,
            sha256="hash",
        )
        assert cache.get(ref) is None
        assert not cache.has(ref)

    def test_cache_set_and_get(self):
        """Test setting and getting cached value."""
        cache = HydrationCache()
        ref = ObjectRef(
            bucket="bucket",
            key="key",
            size_bytes=100,
            sha256="hash",
        )
        data = {"hydrated": "value"}

        cache.set(ref, data)
        assert cache.has(ref)
        assert cache.get(ref) == data

    def test_cache_key_includes_all_fields(self):
        """Test that cache key includes all identifying fields."""
        cache = HydrationCache()

        ref1 = ObjectRef(bucket="b", key="k", size_bytes=100, sha256="h1")
        ref2 = ObjectRef(bucket="b", key="k", size_bytes=100, sha256="h2")

        cache.set(ref1, "value1")
        cache.set(ref2, "value2")

        assert cache.get(ref1) == "value1"
        assert cache.get(ref2) == "value2"


class TestHydrateValue:
    """Tests for hydrate_value function."""

    @pytest.mark.anyio
    async def test_primitive_values_unchanged(self):
        """Test that primitive values pass through unchanged."""
        storage = InMemoryObjectStorage()
        cache = HydrationCache()

        assert await hydrate_value("string", storage, cache) == "string"
        assert await hydrate_value(42, storage, cache) == 42
        assert await hydrate_value(None, storage, cache) is None
        assert await hydrate_value(True, storage, cache) is True

    @pytest.mark.anyio
    async def test_nested_dict_hydration(self):
        """Test hydration of nested dicts (without ObjectRefs)."""
        storage = InMemoryObjectStorage()
        cache = HydrationCache()

        data = {"level1": {"level2": {"value": 42}}}
        result = await hydrate_value(data, storage, cache)

        assert result == data

    @pytest.mark.anyio
    async def test_list_hydration(self):
        """Test hydration of lists."""
        storage = InMemoryObjectStorage()
        cache = HydrationCache()

        data = [1, 2, {"nested": "value"}]
        result = await hydrate_value(data, storage, cache)

        assert result == data


class TestHydrateExecutionContext:
    """Tests for hydrate_execution_context function."""

    @pytest.mark.anyio
    async def test_hydrate_empty_context(self):
        """Test hydrating empty context."""
        context: dict = {}
        result = await hydrate_execution_context(context)
        assert result == {}

    @pytest.mark.anyio
    async def test_hydrate_context_without_refs(self):
        """Test hydrating context without any ObjectRefs."""
        context = {
            ExprContext.TRIGGER: {"event": "test", "data": {"foo": "bar"}},
            ExprContext.ACTIONS: {
                "action1": {"result": {"success": True}},
                "action2": {"result": [1, 2, 3]},
            },
        }
        result = await hydrate_execution_context(context)

        assert result[ExprContext.TRIGGER] == {"event": "test", "data": {"foo": "bar"}}
        assert result[ExprContext.ACTIONS]["action1"]["result"] == {"success": True}
        assert result[ExprContext.ACTIONS]["action2"]["result"] == [1, 2, 3]

    @pytest.mark.anyio
    async def test_hydrate_preserves_other_context_keys(self):
        """Test that hydration preserves other context keys."""
        context = {
            ExprContext.TRIGGER: {"data": 1},
            ExprContext.ACTIONS: {},
            ExprContext.ENV: {"key": "value"},
            ExprContext.SECRETS: {"api_key": "secret"},
        }
        result = await hydrate_execution_context(context)

        assert result[ExprContext.ENV] == {"key": "value"}
        assert result[ExprContext.SECRETS] == {"api_key": "secret"}


class TestDependencyInjection:
    """Tests for DI functions."""

    def test_set_and_get_object_storage(self):
        """Test setting and getting object storage."""
        reset_object_storage()

        custom_storage = InMemoryObjectStorage()
        set_object_storage(custom_storage)

        assert get_object_storage() is custom_storage

        # Cleanup
        reset_object_storage()

    def test_reset_clears_storage(self):
        """Test reset clears the storage instance."""
        custom_storage = InMemoryObjectStorage()
        set_object_storage(custom_storage)
        reset_object_storage()

        # Should create a new instance
        storage = get_object_storage()
        assert storage is not custom_storage

        # Cleanup
        reset_object_storage()

    def test_default_storage_is_in_memory(self, monkeypatch):
        """Test default storage is InMemoryObjectStorage when disabled."""
        from tracecat.storage import object as object_module

        reset_object_storage()
        monkeypatch.setattr(
            object_module.config,
            "TRACECAT__RESULT_EXTERNALIZATION_ENABLED",
            False,
        )

        storage = get_object_storage()
        assert isinstance(storage, InMemoryObjectStorage)

        # Cleanup
        reset_object_storage()
