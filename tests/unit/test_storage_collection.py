"""Tests for the collection storage module.

Tests CollectionObject variant and chunked manifest storage.
"""

import asyncio

import pytest
from botocore.exceptions import ClientError
from pydantic import TypeAdapter
from temporalio.exceptions import ApplicationError

from tracecat.dsl.action import (
    DSLActivities,
    NormalizeTriggerInputsActivityInputs,
)
from tracecat.storage import blob
from tracecat.storage.collection import get_collection_item, store_collection
from tracecat.storage.object import (
    CollectionObject,
    ExternalObject,
    InlineObject,
    ObjectRef,
    StoredObject,
    get_object_storage,
)


class TestCollectionObject:
    """Tests for CollectionObject model."""

    def test_create_collection_object(self):
        """Test creating a CollectionObject with required fields."""
        manifest_ref = ObjectRef(
            bucket="test-bucket",
            key="wf-123/stream-0/action-1/col-abc/manifest.json",
            size_bytes=512,
            sha256="manifest_hash",
        )
        collection = CollectionObject(
            manifest_ref=manifest_ref,
            count=1000,
            chunk_size=256,
            element_kind="value",
        )

        assert collection.type == "collection"
        assert collection.manifest_ref is manifest_ref
        assert collection.count == 1000
        assert collection.chunk_size == 256
        assert collection.element_kind == "value"
        assert collection.schema_version == 1

    def test_collection_object_stored_object_element_kind(self):
        """Test CollectionObject with stored_object element kind."""
        manifest_ref = ObjectRef(
            bucket="bucket",
            key="manifest.json",
            size_bytes=256,
            sha256="hash",
        )
        collection = CollectionObject(
            manifest_ref=manifest_ref,
            count=50,
            chunk_size=10,
            element_kind="stored_object",
        )

        assert collection.element_kind == "stored_object"

    def test_collection_object_serialization_roundtrip(self):
        """Test CollectionObject can be serialized and deserialized."""
        manifest_ref = ObjectRef(
            bucket="bucket",
            key="key/manifest.json",
            size_bytes=100,
            sha256="deadbeef",
        )
        collection = CollectionObject(
            manifest_ref=manifest_ref,
            count=500,
            chunk_size=100,
            element_kind="value",
        )

        data = collection.model_dump()
        restored = CollectionObject.model_validate(data)

        assert restored.type == "collection"
        assert restored.count == 500
        assert restored.chunk_size == 100
        assert restored.manifest_ref.key == "key/manifest.json"

    def test_json_schema_requires_discriminator_type(self):
        """OpenAPI schema should require discriminator `type`."""
        required = CollectionObject.model_json_schema().get("required", [])
        assert "type" in required

    def test_model_dump_exclude_unset_includes_discriminator_type(self):
        """Discriminator `type` must survive exclude_unset serialization."""
        collection = CollectionObject(
            manifest_ref=ObjectRef(
                bucket="bucket",
                key="manifest.json",
                size_bytes=256,
                sha256="hash",
            ),
            count=50,
            chunk_size=10,
            element_kind="value",
        )

        assert collection.model_dump(exclude_unset=True)["type"] == "collection"


class TestStoredObjectUnionWithCollection:
    """Tests for StoredObject union including CollectionObject."""

    def test_type_adapter_validates_collection_object(self):
        """Test TypeAdapter validates CollectionObject from dict."""
        adapter = TypeAdapter(StoredObject)

        collection_dict = {
            "type": "collection",
            "manifest_ref": {
                "backend": "s3",
                "bucket": "test-bucket",
                "key": "manifest.json",
                "size_bytes": 256,
                "sha256": "abc123",
            },
            "count": 1000,
            "chunk_size": 256,
            "element_kind": "value",
            "schema_version": 1,
        }

        collection = adapter.validate_python(collection_dict)
        assert isinstance(collection, CollectionObject)
        assert collection.count == 1000
        assert collection.chunk_size == 256

    def test_type_adapter_json_roundtrip_collection(self):
        """Test TypeAdapter can serialize and deserialize CollectionObject."""
        adapter = TypeAdapter(StoredObject)

        manifest_ref = ObjectRef(
            bucket="bucket",
            key="manifest.json",
            size_bytes=128,
            sha256="hash123",
        )
        collection = CollectionObject(
            manifest_ref=manifest_ref,
            count=200,
            chunk_size=50,
            element_kind="stored_object",
        )

        json_bytes = adapter.dump_json(collection)
        restored = adapter.validate_json(json_bytes)

        assert isinstance(restored, CollectionObject)
        assert restored.count == 200
        assert restored.element_kind == "stored_object"

    def test_pattern_matching_with_collection(self):
        """Test pattern matching works with all three variants."""
        inline = InlineObject(data={"key": "value"})
        external = ExternalObject(
            ref=ObjectRef(bucket="b", key="k", size_bytes=10, sha256="h")
        )
        collection = CollectionObject(
            manifest_ref=ObjectRef(
                bucket="b", key="manifest.json", size_bytes=10, sha256="h"
            ),
            count=100,
            chunk_size=25,
            element_kind="value",
        )

        # Test inline matching
        match inline:
            case InlineObject(data=d):
                assert d == {"key": "value"}
            case ExternalObject():
                pytest.fail("Should not match ExternalObject")
            case CollectionObject():
                pytest.fail("Should not match CollectionObject")

        # Test external matching
        match external:
            case InlineObject():
                pytest.fail("Should not match InlineObject")
            case ExternalObject(ref=r):
                assert r.bucket == "b"
            case CollectionObject():
                pytest.fail("Should not match CollectionObject")

        # Test collection matching
        match collection:
            case InlineObject():
                pytest.fail("Should not match InlineObject")
            case ExternalObject():
                pytest.fail("Should not match ExternalObject")
            case CollectionObject(count=c, chunk_size=cs):
                assert c == 100
                assert cs == 25


class TestCollectionManifestSchemas:
    """Tests for manifest and chunk schemas."""

    def test_manifest_v1_schema(self):
        """Test CollectionManifestV1 schema."""
        from tracecat.storage.collection import CollectionManifestV1

        chunk_refs = [
            ObjectRef(
                bucket="b", key=f"chunks/{i}.json", size_bytes=100, sha256=f"hash{i}"
            )
            for i in range(4)
        ]

        manifest = CollectionManifestV1(
            count=1000,
            chunk_size=256,
            element_kind="value",
            chunks=chunk_refs,
        )

        assert manifest.kind == "tracecat.collection_manifest"
        assert manifest.version == 1
        assert manifest.count == 1000
        assert len(manifest.chunks) == 4

    def test_chunk_v1_schema(self):
        """Test CollectionChunkV1 schema."""
        from tracecat.storage.collection import CollectionChunkV1

        chunk = CollectionChunkV1(
            start=256,
            items=[{"id": i} for i in range(100)],
        )

        assert chunk.kind == "tracecat.collection_chunk"
        assert chunk.version == 1
        assert chunk.start == 256
        assert len(chunk.items) == 100


class TestCollectionStorageFunctions:
    """Tests for collection storage functions.

    These tests require mocking the blob storage layer.
    """

    @pytest.fixture
    def mock_blob_storage(self, monkeypatch):
        """Mock blob storage for testing."""
        import json
        import re

        stored_blobs: dict[str, bytes] = {}

        async def mock_ensure_bucket_exists(bucket: str):
            pass

        async def mock_upload_file(
            content: bytes, key: str, bucket: str, content_type: str
        ):
            stored_blobs[f"{bucket}/{key}"] = content

        async def mock_download_file(key: str, bucket: str) -> bytes:
            full_key = f"{bucket}/{key}"
            if full_key not in stored_blobs:
                raise FileNotFoundError(f"Blob not found: {full_key}")
            return stored_blobs[full_key]

        async def mock_select_object_content(
            key: str, bucket: str, expression: str
        ) -> bytes:
            """Mock S3 Select by parsing the expression and extracting data."""
            full_key = f"{bucket}/{key}"
            if full_key not in stored_blobs:
                raise FileNotFoundError(f"Blob not found: {full_key}")

            data = json.loads(stored_blobs[full_key])

            # Parse expression like "SELECT s.items[0] FROM s3object s"
            match = re.search(r"s\.items\[(\d+)\]", expression)
            if match:
                index = int(match.group(1))
                item = data["items"][index]
                # S3 Select returns {"_1": <item>} for indexed access
                return json.dumps({"_1": item}).encode()

            raise ValueError(f"Unsupported expression: {expression}")

        from tracecat.storage import blob

        monkeypatch.setattr(blob, "ensure_bucket_exists", mock_ensure_bucket_exists)
        monkeypatch.setattr(blob, "upload_file", mock_upload_file)
        monkeypatch.setattr(blob, "download_file", mock_download_file)
        monkeypatch.setattr(blob, "select_object_content", mock_select_object_content)

        return stored_blobs

    @pytest.mark.anyio
    async def test_store_collection_creates_manifest_and_chunks(
        self, mock_blob_storage
    ):
        """Test store_collection creates manifest and chunk blobs."""
        from tracecat.storage.collection import store_collection

        items = [{"id": i, "value": f"item-{i}"} for i in range(100)]

        collection = await store_collection(
            prefix="wf-123/stream-0/action-1/col-abc",
            items=items,
            element_kind="value",
            chunk_size=30,
            bucket="test-bucket",
        )

        # Verify CollectionObject
        assert isinstance(collection, CollectionObject)
        assert collection.count == 100
        assert collection.chunk_size == 30
        assert collection.element_kind == "value"

        # Verify blobs were created (100 items / 30 per chunk = 4 chunks)
        assert (
            "test-bucket/wf-123/stream-0/action-1/col-abc/manifest.json"
            in mock_blob_storage
        )
        assert (
            "test-bucket/wf-123/stream-0/action-1/col-abc/chunks/0.json"
            in mock_blob_storage
        )
        assert (
            "test-bucket/wf-123/stream-0/action-1/col-abc/chunks/1.json"
            in mock_blob_storage
        )
        assert (
            "test-bucket/wf-123/stream-0/action-1/col-abc/chunks/2.json"
            in mock_blob_storage
        )
        assert (
            "test-bucket/wf-123/stream-0/action-1/col-abc/chunks/3.json"
            in mock_blob_storage
        )

    @pytest.mark.anyio
    async def test_store_collection_empty_list(self, mock_blob_storage):
        """Test store_collection handles empty list."""
        from tracecat.storage.collection import store_collection

        collection = await store_collection(
            prefix="wf-123/empty",
            items=[],
            element_kind="value",
            chunk_size=10,
            bucket="test-bucket",
        )

        assert collection.count == 0
        assert "test-bucket/wf-123/empty/manifest.json" in mock_blob_storage

    @pytest.mark.anyio
    async def test_get_collection_page(self, mock_blob_storage):
        """Test get_collection_page retrieves correct items."""
        from tracecat.storage.collection import get_collection_page, store_collection

        items = [{"id": i} for i in range(100)]

        collection = await store_collection(
            prefix="wf-123/page-test",
            items=items,
            element_kind="value",
            chunk_size=30,
            bucket="test-bucket",
        )

        # Get first page
        page = await get_collection_page(collection, offset=0, limit=10)
        assert len(page) == 10
        assert page[0] == {"id": 0}
        assert page[9] == {"id": 9}

        # Get middle page crossing chunk boundary
        page = await get_collection_page(collection, offset=25, limit=10)
        assert len(page) == 10
        assert page[0] == {"id": 25}
        assert page[9] == {"id": 34}

        # Get last items
        page = await get_collection_page(collection, offset=95, limit=10)
        assert len(page) == 5  # Only 5 items left
        assert page[0] == {"id": 95}
        assert page[-1] == {"id": 99}

    @pytest.mark.anyio
    async def test_get_collection_item(self, mock_blob_storage):
        """Test get_collection_item retrieves single items."""
        from tracecat.storage.collection import get_collection_item, store_collection

        items = [{"id": i, "name": f"item-{i}"} for i in range(50)]

        collection = await store_collection(
            prefix="wf-123/item-test",
            items=items,
            element_kind="value",
            chunk_size=10,
            bucket="test-bucket",
        )

        # Get first item
        item = await get_collection_item(collection, 0)
        assert item == {"id": 0, "name": "item-0"}

        # Get item in middle
        item = await get_collection_item(collection, 25)
        assert item == {"id": 25, "name": "item-25"}

        # Get last item
        item = await get_collection_item(collection, 49)
        assert item == {"id": 49, "name": "item-49"}

        # Test negative indexing
        item = await get_collection_item(collection, -1)
        assert item == {"id": 49, "name": "item-49"}

    @pytest.mark.anyio
    async def test_get_collection_item_stored_object_materializes_value(
        self, mock_blob_storage
    ):
        """Test get_collection_item dereferences stored_object handles."""

        large_payload = "x" * (
            300 * 1024
        )  # 300 KB payload to exceed S3 Select limits for testing
        values = [{"id": i, "payload": large_payload} for i in range(3)]

        storage = get_object_storage()
        handles: list[dict[str, object]] = []
        for i, value in enumerate(values):
            stored = await storage.store(f"wf-123/stored-object-source/{i}.json", value)
            handles.append(stored.model_dump())

        collection = await store_collection(
            prefix="wf-123/stored-object-collection",
            items=handles,
            element_kind="stored_object",
            chunk_size=2,
            bucket="test-bucket",
        )

        item = await get_collection_item(collection, 1)
        assert item["id"] == 1
        assert len(item["payload"]) == len(large_payload)

        last = await get_collection_item(collection, -1)
        assert last["id"] == 2
        assert len(last["payload"]) == len(large_payload)

    @pytest.mark.anyio
    async def test_get_collection_item_out_of_bounds(self, mock_blob_storage):
        """Test get_collection_item raises IndexError for out of bounds."""
        from tracecat.storage.collection import get_collection_item, store_collection

        items = [{"id": i} for i in range(10)]

        collection = await store_collection(
            prefix="wf-123/bounds-test",
            items=items,
            element_kind="value",
            chunk_size=5,
            bucket="test-bucket",
        )

        with pytest.raises(IndexError):
            await get_collection_item(collection, 10)

        with pytest.raises(IndexError):
            await get_collection_item(collection, -11)

    @pytest.mark.anyio
    async def test_materialize_collection_values(self, mock_blob_storage):
        """Test materialize_collection_values returns all items."""
        from tracecat.storage.collection import (
            materialize_collection_values,
            store_collection,
        )

        items = [{"id": i} for i in range(75)]

        collection = await store_collection(
            prefix="wf-123/materialize-test",
            items=items,
            element_kind="value",
            chunk_size=20,
            bucket="test-bucket",
        )

        # Materialize all
        values = await materialize_collection_values(collection)
        assert len(values) == 75
        assert values[0] == {"id": 0}
        assert values[74] == {"id": 74}

        # Materialize with offset/limit
        values = await materialize_collection_values(collection, offset=10, limit=20)
        assert len(values) == 20
        assert values[0] == {"id": 10}
        assert values[19] == {"id": 29}

    @pytest.mark.anyio
    async def test_looped_subflow_indexed_trigger_input_without_s3_select(
        self, mock_blob_storage, monkeypatch
    ):
        """Red test: looped subflow indexed retrieval should not depend on S3 Select.

        Current implementation calls S3 Select for indexed collection lookups, which
        fails on AWS S3 for >1MB JSON records (OverMaxRecordSize). The expected
        behavior is to still retrieve the i-th trigger input successfully.
        """

        large_payload = "x" * (2 * 1024 * 1024)  # 2 MB per item
        items = [{"idx": i, "payload": large_payload} for i in range(25)]

        collection = await store_collection(
            prefix="wf-123/looped-subflow",
            items=items,
            element_kind="value",
            chunk_size=25,
            bucket="test-bucket",
        )

        async def over_max_record_size(key: str, bucket: str, expression: str) -> bytes:
            raise ClientError(
                {
                    "Error": {
                        "Code": "OverMaxRecordSize",
                        "Message": (
                            "The character number in one record is more than our max "
                            "threshold, maxCharsPerRecord: 1,048,576"
                        ),
                    }
                },
                "SelectObjectContent",
            )

        monkeypatch.setattr(blob, "select_object_content", over_max_record_size)

        # This should succeed after refactor, because indexed lookup should avoid S3 Select.
        try:
            normalized = await asyncio.to_thread(
                DSLActivities.normalize_trigger_inputs_activity,
                NormalizeTriggerInputsActivityInputs(
                    input_schema={},
                    trigger_inputs=collection.at(0),
                    key="wf-123/looped-subflow/normalized-trigger-input.json",
                ),
            )
        except ApplicationError:  # pragma: no cover - expected red failure today
            pytest.fail(
                "Looped subflow indexed trigger input retrieval still depends on "
                "S3 Select and fails with OverMaxRecordSize"
            )

        resolved = await get_object_storage().retrieve(normalized)
        assert resolved == items[0]
