from typing import Any, cast

import pytest
from tracecat_registry.core.ai import rank_documents, select_field, select_fields


@pytest.mark.anyio
async def test_select_field_unsupported_algorithm_raises_value_error() -> None:
    with pytest.raises(ValueError, match="Unsupported algorithm: invalid"):
        await select_field(
            json={"name": "tracecat"},
            criteria_prompt="Pick the name field.",
            algorithm=cast(Any, "invalid"),
        )


@pytest.mark.anyio
async def test_select_fields_unsupported_algorithm_raises_value_error() -> None:
    with pytest.raises(ValueError, match="Unsupported algorithm: invalid"):
        await select_fields(
            json={"name": "tracecat"},
            criteria_prompt="Pick the name field.",
            algorithm=cast(Any, "invalid"),
        )


@pytest.mark.anyio
async def test_rank_documents_passes_source_backed_model_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_rank_items(**kwargs: Any) -> list[int]:
        captured.update(kwargs)
        return [1, 0, 2]

    monkeypatch.setattr("tracecat_registry.core.ai.rank_items", fake_rank_items)

    result = await rank_documents(
        items=["first", "second", "third"],
        criteria_prompt="Rank them",
        model='["11111111-1111-1111-1111-111111111111","openai","gpt-5"]',
    )

    assert result == ["second", "first", "third"]
    assert captured["source_id"] == "11111111-1111-1111-1111-111111111111"
    assert captured["model_provider"] == "openai"
    assert captured["model_name"] == "gpt-5"


@pytest.mark.anyio
async def test_rank_documents_accepts_legacy_provider_model_selection_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_rank_items(**kwargs: Any) -> list[int]:
        captured.update(kwargs)
        return [0, 1, 2]

    monkeypatch.setattr("tracecat_registry.core.ai.rank_items", fake_rank_items)

    await rank_documents(
        items=["first", "second", "third"],
        criteria_prompt="Rank them",
        model="openai::gpt-5",
    )

    assert captured["source_id"] is None
    assert captured["model_provider"] == "openai"
    assert captured["model_name"] == "gpt-5"


@pytest.mark.anyio
async def test_rank_documents_accepts_legacy_split_model_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_rank_items(**kwargs: Any) -> list[int]:
        captured.update(kwargs)
        return [0, 1, 2]

    monkeypatch.setattr("tracecat_registry.core.ai.rank_items", fake_rank_items)

    result = await rank_documents(
        items=["first", "second", "third"],
        criteria_prompt="Rank them",
        model_name="gpt-5",
        model_provider="openai",
    )

    assert result == ["first", "second", "third"]
    assert captured["source_id"] is None
    assert captured["model_provider"] == "openai"
    assert captured["model_name"] == "gpt-5"


@pytest.mark.anyio
async def test_rank_documents_accepts_legacy_model_name_without_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_rank_items(**kwargs: Any) -> list[int]:
        captured.update(kwargs)
        return [0, 1, 2]

    monkeypatch.setattr("tracecat_registry.core.ai.rank_items", fake_rank_items)

    await rank_documents(
        items=["first", "second", "third"],
        criteria_prompt="Rank them",
        model_name="gpt-5",
    )

    assert captured["source_id"] is None
    assert captured["model_provider"] == "openai"
    assert captured["model_name"] == "gpt-5"


@pytest.mark.anyio
async def test_select_field_accepts_legacy_model_name_without_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_rank_items(**kwargs: Any) -> list[str]:
        captured.update(kwargs)
        return ["name"]

    monkeypatch.setattr("tracecat_registry.core.ai.rank_items", fake_rank_items)

    result = await select_field(
        json={"name": "tracecat", "type": "agent"},
        criteria_prompt="Pick the name field.",
        model_name="gpt-5",
    )

    assert result == {"key": "name", "value": "tracecat"}
    assert captured["source_id"] is None
    assert captured["model_provider"] == "openai"
    assert captured["model_name"] == "gpt-5"


@pytest.mark.anyio
async def test_select_fields_accepts_legacy_model_name_without_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_rank_items(**kwargs: Any) -> list[str]:
        captured.update(kwargs)
        return ["name", "type"]

    monkeypatch.setattr("tracecat_registry.core.ai.rank_items", fake_rank_items)

    result = await select_fields(
        json={"name": "tracecat", "type": "agent", "priority": "high"},
        criteria_prompt="Pick the most important fields.",
        min_fields=2,
        max_fields=2,
        model_name="gpt-5",
    )

    assert result == {"name": "tracecat", "type": "agent"}
    assert captured["source_id"] is None
    assert captured["model_provider"] == "openai"
    assert captured["model_name"] == "gpt-5"
