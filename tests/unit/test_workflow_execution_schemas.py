from __future__ import annotations

import pytest
from pydantic import ValidationError

from tracecat.storage.object import InlineObject
from tracecat.workflow.executions.schemas import (
    WorkflowExecutionCollectionPageItem,
    WorkflowExecutionCollectionPageItemKind,
)


def test_collection_page_item_stored_kind_requires_stored() -> None:
    with pytest.raises(ValidationError, match="stored.*required"):
        WorkflowExecutionCollectionPageItem(
            index=0,
            kind=WorkflowExecutionCollectionPageItemKind.STORED_OBJECT_REF,
        )


def test_collection_page_item_stored_kind_rejects_inline_fields() -> None:
    with pytest.raises(ValidationError, match="must be omitted"):
        WorkflowExecutionCollectionPageItem(
            index=0,
            kind=WorkflowExecutionCollectionPageItemKind.STORED_OBJECT_REF,
            stored=InlineObject(data={"ok": True}),
            value_preview="{}",
            value_size_bytes=2,
        )


def test_collection_page_item_inline_kind_rejects_stored() -> None:
    with pytest.raises(ValidationError, match="stored.*omitted"):
        WorkflowExecutionCollectionPageItem(
            index=0,
            kind=WorkflowExecutionCollectionPageItemKind.INLINE_VALUE,
            stored=InlineObject(data={"ok": True}),
            value_preview="{}",
            value_size_bytes=2,
        )


def test_collection_page_item_inline_kind_requires_inline_fields() -> None:
    with pytest.raises(ValidationError, match="value_preview.*value_size_bytes"):
        WorkflowExecutionCollectionPageItem(
            index=0,
            kind=WorkflowExecutionCollectionPageItemKind.INLINE_VALUE,
        )


def test_collection_page_item_stored_kind_valid() -> None:
    item = WorkflowExecutionCollectionPageItem(
        index=1,
        kind=WorkflowExecutionCollectionPageItemKind.STORED_OBJECT_REF,
        stored=InlineObject(data={"ok": True}),
    )

    assert item.kind == WorkflowExecutionCollectionPageItemKind.STORED_OBJECT_REF
    assert isinstance(item.stored, InlineObject)


def test_collection_page_item_inline_kind_valid() -> None:
    item = WorkflowExecutionCollectionPageItem(
        index=2,
        kind=WorkflowExecutionCollectionPageItemKind.INLINE_VALUE,
        value_preview='{"ok": true}',
        value_size_bytes=12,
        truncated=False,
    )

    assert item.kind == WorkflowExecutionCollectionPageItemKind.INLINE_VALUE
    assert item.value_size_bytes == 12
