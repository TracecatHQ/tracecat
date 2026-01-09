"""Tests for the object storage module.

Uses InMemoryObjectStorage as the test double - no mocks needed.
"""

import pytest

from tracecat.storage.object import (
    InMemoryObjectStorage,
    ObjectRef,
    StoredObject,
    compute_sha256,
    deserialize_object,
    get_object_storage,
    reset_object_storage,
    serialize_object,
    set_object_storage,
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
    async def test_retrieve_inline_data(self):
        """InMemoryObjectStorage.retrieve returns inline data."""
        storage = InMemoryObjectStorage()
        stored = await storage.store("key", {"test": "data"})

        result = await storage.retrieve(stored)
        assert result == {"test": "data"}

    @pytest.mark.anyio
    async def test_retrieve_raises_not_implemented_for_externalized(self):
        """InMemoryObjectStorage.retrieve raises NotImplementedError for externalized refs."""
        storage = InMemoryObjectStorage()
        ref = ObjectRef(
            bucket="bucket",
            key="key",
            size_bytes=100,
            sha256="hash",
        )
        stored = StoredObject(ref=ref)

        with pytest.raises(NotImplementedError):
            await storage.retrieve(stored)

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
