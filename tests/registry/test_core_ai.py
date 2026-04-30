import uuid
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest
from tracecat_registry.core.ai import rank_documents, select_field, select_fields
from tracecat_registry.fields import ModelSelection


@pytest.mark.anyio
async def test_select_field_unsupported_algorithm_raises_value_error() -> None:
    with pytest.raises(ValueError, match="Unsupported algorithm: invalid"):
        await select_field(
            json={"name": "tracecat"},
            criteria_prompt="Pick the name field.",
            algorithm=cast(Any, "invalid"),
        )


@pytest.mark.anyio
async def test_rank_documents_passes_catalog_model_selection() -> None:
    catalog_id = uuid.uuid4()
    model = ModelSelection(
        model_name="claude-sonnet-4-5",
        model_provider="anthropic",
        catalog_id=catalog_id,
    )

    with patch(
        "tracecat_registry.core.ai.rank_items",
        AsyncMock(return_value=[2, 0, 1]),
    ) as rank_items_mock:
        result = await rank_documents(
            items=["third", "first", "second"],
            criteria_prompt="best first",
            model=model,
        )

    assert result == ["second", "third", "first"]
    rank_items_mock.assert_awaited_once()
    assert rank_items_mock.await_args is not None
    kwargs = rank_items_mock.await_args.kwargs
    assert kwargs["model_name"] == model.model_name
    assert kwargs["model_provider"] == model.model_provider
    assert kwargs["catalog_id"] == catalog_id


@pytest.mark.anyio
async def test_select_fields_unsupported_algorithm_raises_value_error() -> None:
    with pytest.raises(ValueError, match="Unsupported algorithm: invalid"):
        await select_fields(
            json={"name": "tracecat"},
            criteria_prompt="Pick the name field.",
            algorithm=cast(Any, "invalid"),
        )
