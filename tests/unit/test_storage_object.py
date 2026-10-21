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
)
from tracecat.storage.utils import (
    compute_sha256,
    deserialize_object,
    serialize_object,
)


class TestStoredObject:
    """Tests for StoredObject tagged union (InlineObject | ExternalObject)."""

    def test_discriminator_type_is_required_and_serialized(self):
        """OpenAPI requires `type`, and it survives exclude_unset serialization."""
        inline = InlineObject(data={"foo": "bar"})
        external = ExternalObject(
            ref=ObjectRef(bucket="bucket", key="key", size_bytes=1000, sha256="hash")
        )

        assert "type" in InlineObject.model_json_schema().get("required", [])
        assert "type" in ExternalObject.model_json_schema().get("required", [])
        assert inline.model_dump(exclude_unset=True)["type"] == "inline"
        assert external.model_dump(exclude_unset=True)["type"] == "external"

    @pytest.mark.parametrize(
        "stored",
        [
            InlineObject(data={"key": "value"}),
            InlineObject(data=None),
            ExternalObject(
                ref=ObjectRef(bucket="b", key="k", size_bytes=10, sha256="h")
            ),
        ],
        ids=["inline", "inline_none", "external"],
    )
    def test_type_adapter_json_roundtrip(self, stored: StoredObject):
        """The StoredObject union round-trips each variant through JSON."""
        adapter = TypeAdapter(StoredObject)

        restored = adapter.validate_json(adapter.dump_json(stored))

        assert type(restored) is type(stored)
        assert restored == stored


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


def test_default_storage_is_in_memory_when_externalization_disabled(monkeypatch):
    from tracecat.storage import object as object_module

    reset_object_storage()
    monkeypatch.setattr(
        object_module.config,
        "TRACECAT__RESULT_EXTERNALIZATION_ENABLED",
        False,
    )

    try:
        assert isinstance(get_object_storage(), InlineObjectStorage)
    finally:
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


class TestS3ObjectStorageThresholdOverride:
    """A per-call threshold can only lower the backend threshold."""

    @pytest.fixture(autouse=True)
    def blobs(self, monkeypatch: pytest.MonkeyPatch) -> dict[str, bytes]:
        from tracecat.storage import blob

        stored: dict[str, bytes] = {}

        async def ensure_bucket_exists(bucket: str) -> None:
            pass

        async def upload_file(
            content: bytes, key: str, bucket: str, content_type: str
        ) -> None:
            stored[f"{bucket}/{key}"] = content

        monkeypatch.setattr(blob, "ensure_bucket_exists", ensure_bucket_exists)
        monkeypatch.setattr(blob, "upload_file", upload_file)
        return stored

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        ("backend_threshold", "call_threshold", "expected_type"),
        [
            pytest.param(1024, None, InlineObject, id="no-override"),
            pytest.param(1024, 10, ExternalObject, id="lower-override-wins"),
            pytest.param(10, 1024, ExternalObject, id="higher-override-ignored"),
        ],
    )
    async def test_store_uses_lower_threshold(
        self,
        backend_threshold: int,
        call_threshold: int | None,
        expected_type: type[StoredObject],
    ):
        storage = S3ObjectStorage(bucket="b", threshold_bytes=backend_threshold)
        key = action_key("ws", "run", "<root>:0", "a")

        stored = await storage.store(key, "x" * 100, threshold_bytes=call_threshold)

        assert isinstance(stored, expected_type)
