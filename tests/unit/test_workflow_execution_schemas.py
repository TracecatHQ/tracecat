from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from tracecat.storage.object import InlineObject
from tracecat.workflow.executions.schemas import (
    WorkflowExecutionCollectionPageItem,
    WorkflowExecutionCollectionPageItemKind,
)

_STORED = WorkflowExecutionCollectionPageItemKind.STORED_OBJECT_REF
_INLINE = WorkflowExecutionCollectionPageItemKind.INLINE_VALUE


@pytest.mark.parametrize(
    ("fields", "error"),
    [
        pytest.param(
            {"kind": _STORED}, "stored.*required", id="stored_requires_stored"
        ),
        pytest.param(
            {
                "kind": _STORED,
                "stored": InlineObject(data={"ok": True}),
                "value_preview": "{}",
                "value_size_bytes": 2,
            },
            "must be omitted",
            id="stored_rejects_inline_fields",
        ),
        pytest.param(
            {
                "kind": _INLINE,
                "stored": InlineObject(data={"ok": True}),
                "value_preview": "{}",
                "value_size_bytes": 2,
            },
            "stored.*omitted",
            id="inline_rejects_stored",
        ),
        pytest.param(
            {"kind": _INLINE},
            "value_preview.*value_size_bytes",
            id="inline_requires_inline_fields",
        ),
        pytest.param(
            {"kind": _STORED, "stored": InlineObject(data={"ok": True})},
            None,
            id="stored_valid",
        ),
        pytest.param(
            {
                "kind": _INLINE,
                "value_preview": '{"ok": true}',
                "value_size_bytes": 12,
                "truncated": False,
            },
            None,
            id="inline_valid",
        ),
    ],
)
def test_collection_page_item_kind_fields(
    fields: dict[str, Any], error: str | None
) -> None:
    if error is not None:
        with pytest.raises(ValidationError, match=error):
            WorkflowExecutionCollectionPageItem(index=0, **fields)
        return

    item = WorkflowExecutionCollectionPageItem(index=0, **fields)
    assert item.kind == fields["kind"]
    assert item.stored == fields.get("stored")
    assert item.value_size_bytes == fields.get("value_size_bytes")
