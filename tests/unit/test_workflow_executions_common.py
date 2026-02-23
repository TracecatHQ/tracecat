from __future__ import annotations

import pytest

from tracecat.storage.object import CollectionObject, InlineObject, ObjectRef
from tracecat.workflow.executions.common import unwrap_action_result


def _collection() -> CollectionObject:
    return CollectionObject(
        manifest_ref=ObjectRef(
            bucket="test-bucket",
            key="wf-123/actions/scatter/manifest.json",
            size_bytes=256,
            sha256="abc123",
        ),
        count=3,
        chunk_size=256,
        element_kind="stored_object",
    )


@pytest.mark.anyio
async def test_unwrap_action_result_keeps_collection_metadata() -> None:
    collection = _collection()

    result = await unwrap_action_result(collection)

    assert result == collection


@pytest.mark.anyio
async def test_unwrap_action_result_keeps_inline_payload_data() -> None:
    inline = InlineObject(data={"ok": True})

    result = await unwrap_action_result(inline)

    assert result == {"ok": True}
