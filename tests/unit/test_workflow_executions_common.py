from __future__ import annotations

from collections.abc import Sequence

import pytest
import temporalio.api.common.v1
from temporalio.api.common.v1 import Payload

from tracecat.storage.object import CollectionObject, InlineObject, ObjectRef
from tracecat.temporal.codec import TemporalPayloadCodecError
from tracecat.workflow.executions import common as executions_common
from tracecat.workflow.executions.common import extract_payload, unwrap_action_result


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


@pytest.mark.anyio
async def test_extract_payload_falls_back_to_raw_payload_on_decode_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_decode_payloads(_payloads: Sequence[Payload]) -> list[Payload]:
        raise TemporalPayloadCodecError("boom")

    monkeypatch.setattr(executions_common, "decode_payloads", fail_decode_payloads)

    payloads = temporalio.api.common.v1.Payloads(
        payloads=[
            Payload(metadata={"encoding": b"json/plain"}, data=b'{"ok":true}'),
        ]
    )

    result = await extract_payload(payloads)

    assert result == {"ok": True}
