"""Tests for the object storage module.

Uses InlineObjectStorage as the test double - no mocks needed.
"""

import pytest
from pydantic import TypeAdapter

from tracecat.storage.backends import InlineObjectStorage, S3ObjectStorage
from tracecat.storage.object import (
    ExternalObject,
    InlineObject,
    ObjectRef,
    StoredObject,
    action_key,
    content_addressed_key,
    get_object_storage,
    reset_object_storage,
    set_object_storage,
)
from tracecat.storage.utils import (
    compute_sha256,
    deserialize_object,
    serialize_object,
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

    def test_object_ref_serialization_roundtrip(self):
        """Test ObjectRef can be serialized and deserialized."""
        ref = ObjectRef(
            bucket="bucket",
            key="key",
            size_bytes=100,
            sha256="deadbeef",
        )
        data = ref.model_dump()
        restored = ObjectRef.model_validate(data)

        assert restored.bucket == ref.bucket
        assert restored.key == ref.key
        assert restored.sha256 == ref.sha256


class TestStoredObject:
    """Tests for StoredObject tagged union (InlineObject | ExternalObject)."""

    def test_inline_object(self):
        """Test InlineObject for inline data."""
        stored = InlineObject(data={"foo": "bar"})
        assert stored.type == "inline"
        assert stored.data == {"foo": "bar"}
        assert isinstance(stored, InlineObject)

    def test_inline_object_with_none_value(self):
        """Test InlineObject can hold None as a valid value."""
        stored = InlineObject(data=None)
        assert stored.type == "inline"
        assert stored.data is None
        assert isinstance(stored, InlineObject)

    def test_external_object(self):
        """Test ExternalObject for externalized data."""
        ref = ObjectRef(
            bucket="bucket",
            key="key",
            size_bytes=1000,
            sha256="hash",
        )
        stored = ExternalObject(ref=ref)
        assert stored.type == "external"
        assert stored.ref is ref
        assert isinstance(stored, ExternalObject)

    def test_json_schema_requires_discriminator_type(self):
        """OpenAPI schema should require discriminator `type`."""
        inline_required = InlineObject.model_json_schema().get("required", [])
        external_required = ExternalObject.model_json_schema().get("required", [])
        assert "type" in inline_required
        assert "type" in external_required

    def test_model_dump_exclude_unset_includes_discriminator_type(self):
        """Discriminator `type` must survive exclude_unset serialization."""
        inline = InlineObject(data={"foo": "bar"})
        external = ExternalObject(
            ref=ObjectRef(bucket="bucket", key="key", size_bytes=1000, sha256="hash")
        )

        assert inline.model_dump(exclude_unset=True)["type"] == "inline"
        assert external.model_dump(exclude_unset=True)["type"] == "external"

    def test_pattern_matching(self):
        """Test pattern matching works with the tagged union."""
        inline = InlineObject(data=42)
        external = ExternalObject(
            ref=ObjectRef(bucket="b", key="k", size_bytes=10, sha256="h")
        )

        # Test inline matching
        match inline:
            case InlineObject(data=d):
                assert d == 42
            case ExternalObject():
                pytest.fail("Should not match ExternalObject")

        # Test external matching
        match external:
            case InlineObject():
                pytest.fail("Should not match InlineObject")
            case ExternalObject(ref=r):
                assert r.bucket == "b"

    def test_type_adapter_validation(self):
        """Test TypeAdapter can validate StoredObject from dict."""
        adapter = TypeAdapter(StoredObject)

        # Validate inline object from dict
        inline_dict = {"type": "inline", "data": {"foo": "bar"}}
        inline = adapter.validate_python(inline_dict)
        assert isinstance(inline, InlineObject)
        assert inline.data == {"foo": "bar"}

        # Validate external object from dict
        external_dict = {
            "type": "external",
            "ref": {
                "backend": "s3",
                "bucket": "test",
                "key": "test.json",
                "size_bytes": 100,
                "sha256": "abc123",
            },
        }
        external = adapter.validate_python(external_dict)
        assert isinstance(external, ExternalObject)
        assert external.ref.bucket == "test"

    def test_type_adapter_json_roundtrip(self):
        """Test TypeAdapter can serialize and deserialize StoredObject."""
        adapter = TypeAdapter(StoredObject)

        # Test inline roundtrip
        inline = InlineObject(data={"key": "value"})
        json_bytes = adapter.dump_json(inline)
        restored = adapter.validate_json(json_bytes)
        assert isinstance(restored, InlineObject)
        assert restored.data == {"key": "value"}

        # Test external roundtrip
        external = ExternalObject(
            ref=ObjectRef(bucket="b", key="k", size_bytes=10, sha256="h")
        )
        json_bytes = adapter.dump_json(external)
        restored = adapter.validate_json(json_bytes)
        assert isinstance(restored, ExternalObject)
        assert restored.ref.bucket == "b"


class TestInlineObjectStorage:
    """Tests for InlineObjectStorage."""

    @pytest.mark.anyio
    async def test_store_always_inline(self):
        """InlineObjectStorage always returns inline data."""
        storage = InlineObjectStorage()
        data = {"large": "x" * 1_000_000}  # 1MB data

        stored = await storage.store("key", data)

        assert isinstance(stored, InlineObject)
        assert stored.data == data

    @pytest.mark.anyio
    async def test_retrieve_inline_data(self):
        """InlineObjectStorage.retrieve returns inline data."""
        storage = InlineObjectStorage()
        stored = await storage.store("key", {"test": "data"})

        result = await storage.retrieve(stored)
        assert result == {"test": "data"}

    @pytest.mark.anyio
    async def test_retrieve_raises_not_implemented_for_externalized(self):
        """InlineObjectStorage.retrieve raises NotImplementedError for externalized refs."""
        storage = InlineObjectStorage()
        ref = ObjectRef(
            bucket="bucket",
            key="key",
            size_bytes=100,
            sha256="hash",
        )
        stored = ExternalObject(ref=ref)

        with pytest.raises(NotImplementedError):
            await storage.retrieve(stored)


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

        custom_storage = InlineObjectStorage()
        set_object_storage(custom_storage)

        assert get_object_storage() is custom_storage

        # Cleanup
        reset_object_storage()

    def test_reset_clears_storage(self):
        """Test reset clears the storage instance."""
        custom_storage = InlineObjectStorage()
        set_object_storage(custom_storage)
        reset_object_storage()

        # Should create a new instance
        storage = get_object_storage()
        assert storage is not custom_storage

        # Cleanup
        reset_object_storage()

    def test_default_storage_is_in_memory(self, monkeypatch):
        """Test default storage is InlineObjectStorage when disabled."""
        from tracecat.storage import object as object_module

        reset_object_storage()
        monkeypatch.setattr(
            object_module.config,
            "TRACECAT__RESULT_EXTERNALIZATION_ENABLED",
            False,
        )

        storage = get_object_storage()
        assert isinstance(storage, InlineObjectStorage)

        # Cleanup
        reset_object_storage()


class TestContentAddressedKey:
    def test_inserts_hash_before_extension(self):
        assert (
            content_addressed_key("ws/run/actions/s/a.json", "abc")
            == "ws/run/actions/s/a.abc.json"
        )

    def test_appends_hash_without_extension(self):
        assert content_addressed_key("ws/run/a", "abc") == "ws/run/a.abc"


class TestS3ObjectStorageDuplicateAttempts:
    """Duplicate activity attempts must not overwrite accepted results."""

    @pytest.fixture
    def blobs(self, monkeypatch: pytest.MonkeyPatch) -> dict[str, bytes]:
        from tracecat.storage import blob, utils

        stored: dict[str, bytes] = {}

        async def ensure_bucket_exists(bucket: str) -> None:
            pass

        async def upload_file(
            content: bytes, key: str, bucket: str, content_type: str
        ) -> None:
            stored[f"{bucket}/{key}"] = content

        async def download_file(key: str, bucket: str) -> bytes:
            return stored[f"{bucket}/{key}"]

        monkeypatch.setattr(blob, "ensure_bucket_exists", ensure_bucket_exists)
        monkeypatch.setattr(blob, "upload_file", upload_file)
        monkeypatch.setattr(blob, "download_file", download_file)
        # A warm per-pod cache would hide the race, so start cold.
        monkeypatch.setattr(
            utils, "_blob_cache", utils.SizedMemoryCache(max_bytes=1 << 20, ttl=60)
        )
        return stored

    @pytest.mark.anyio
    async def test_late_attempt_cannot_overwrite_accepted_ref(
        self, blobs: dict[str, bytes]
    ):
        storage = S3ObjectStorage(bucket="b", threshold_bytes=0)
        key = action_key("ws", "run", "<root>:0", "a")

        accepted = await storage.store(key, {"attempt": 2})
        late = await storage.store(key, {"attempt": 1})

        assert isinstance(accepted, ExternalObject)
        assert isinstance(late, ExternalObject)
        assert accepted.ref.key != late.ref.key
        assert await storage.retrieve(accepted) == {"attempt": 2}
        assert await storage.retrieve(late) == {"attempt": 1}

    @pytest.mark.anyio
    async def test_identical_retry_is_idempotent(self, blobs: dict[str, bytes]):
        storage = S3ObjectStorage(bucket="b", threshold_bytes=0)
        key = action_key("ws", "run", "<root>:0", "a")

        first = await storage.store(key, {"same": True})
        second = await storage.store(key, {"same": True})

        assert isinstance(first, ExternalObject)
        assert isinstance(second, ExternalObject)
        assert first.ref.key == second.ref.key
        assert len(blobs) == 1

    @pytest.mark.anyio
    async def test_legacy_ref_still_retrieves(self, blobs: dict[str, bytes]):
        legacy_key = "ws/run/actions/<root>:0/a.json"
        content = serialize_object({"legacy": True})
        blobs[f"b/{legacy_key}"] = content
        ref = ObjectRef(
            bucket="b",
            key=legacy_key,
            size_bytes=len(content),
            sha256=compute_sha256(content),
        )

        storage = S3ObjectStorage(bucket="b", threshold_bytes=0)
        assert await storage.retrieve(ExternalObject(ref=ref)) == {"legacy": True}
