import uuid
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest
from tracecat_registry.core.ai import rank_documents, select_field, select_fields
from tracecat_registry.fields import ModelSelection

from tracecat.registry.repository import RegisterKwargs, generate_model_from_function


def _input_schema_for(fn: Any) -> dict[str, Any]:
    kwargs = RegisterKwargs.model_validate(getattr(fn, "__tracecat_udf_kwargs"))
    args_cls, _, _ = generate_model_from_function(fn, kwargs)
    return args_cls.model_json_schema()


@pytest.mark.anyio
async def test_select_field_unsupported_algorithm_raises_value_error() -> None:
    with pytest.raises(ValueError, match="Unsupported algorithm: invalid"):
        await select_field(
            json={"name": "tracecat"},
            criteria_prompt="Pick the name field.",
            algorithm=cast(Any, "invalid"),
        )


@pytest.mark.anyio
async def test_rank_documents_accepts_legacy_model_fields() -> None:
    with patch(
        "tracecat_registry.core.ai.rank_items",
        AsyncMock(return_value=[2, 0, 1]),
    ) as rank_items_mock:
        result = await rank_documents(
            items=["third", "first", "second"],
            criteria_prompt="best first",
            model_name="gpt-4o-mini",
            model_provider="openai",
        )

    assert result == ["second", "third", "first"]
    rank_items_mock.assert_awaited_once()
    assert rank_items_mock.await_args is not None
    kwargs = rank_items_mock.await_args.kwargs
    assert kwargs["model_name"] == "gpt-4o-mini"
    assert kwargs["model_provider"] == "openai"
    assert kwargs["catalog_id"] is None


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
async def test_rank_documents_prefers_model_over_legacy_model_fields() -> None:
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
            model_name="gpt-4o-mini",
            model_provider="openai",
        )

    assert result == ["second", "third", "first"]
    rank_items_mock.assert_awaited_once()
    assert rank_items_mock.await_args is not None
    kwargs = rank_items_mock.await_args.kwargs
    assert kwargs["model_name"] == model.model_name
    assert kwargs["model_provider"] == model.model_provider
    assert kwargs["catalog_id"] == catalog_id


def test_rank_documents_schema_marks_legacy_model_fields_deprecated() -> None:
    schema = _input_schema_for(rank_documents)
    properties = schema["properties"]

    assert "model" not in schema.get("required", [])
    assert properties["model_name"]["deprecated"] is True
    assert properties["model_provider"]["deprecated"] is True
    assert (
        properties["model_name"]["x-tracecat-deprecation-message"]
        == "Use `model` instead."
    )


@pytest.mark.anyio
async def test_select_fields_unsupported_algorithm_raises_value_error() -> None:
    with pytest.raises(ValueError, match="Unsupported algorithm: invalid"):
        await select_fields(
            json={"name": "tracecat"},
            criteria_prompt="Pick the name field.",
            algorithm=cast(Any, "invalid"),
        )
