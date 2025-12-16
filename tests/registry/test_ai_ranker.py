from __future__ import annotations

from typing import cast

import pytest
from tracecat_registry.context import RegistryContext
from tracecat_registry.sdk.client import TracecatClient
from tracecat_registry.utils import ai_ranker


@pytest.mark.anyio
async def test_rank_items_returns_empty_list_without_calling_api(
    registry_context: RegistryContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_post(self: TracecatClient, path: str, *, params=None, json=None):  # type: ignore[no-untyped-def]
        raise AssertionError("Should not call API for empty input")

    monkeypatch.setattr(TracecatClient, "post", fake_post, raising=True)

    ranked = await ai_ranker.rank_items(
        items=[],
        criteria_prompt="anything",
        model_name="gpt-4o-mini",
        model_provider="openai",
    )
    assert ranked == []


def test_create_batches_chunks_items_evenly() -> None:
    items = cast(
        list[ai_ranker.RankableItem],
        [{"id": idx, "text": str(idx)} for idx in range(5)],
    )
    batches = ai_ranker._create_batches(items, batch_size=2)

    assert len(batches) == 3
    assert [len(batch) for batch in batches] == [2, 2, 1]
    assert batches[0][0]["id"] == 0
    assert batches[-1][-1]["id"] == 4


def test_sanitize_ids_strips_backticks_and_dedupes() -> None:
    valid = {"A", "B", 1}
    returned = ["`A`", "A", "B", "1", "ghost"]
    assert ai_ranker._sanitize_ids(returned, valid) == ["A", "B", 1]


@pytest.mark.anyio
async def test_rank_items_parses_output_from_executor_agent(
    registry_context: RegistryContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_post(self: TracecatClient, path: str, *, params=None, json=None):  # type: ignore[no-untyped-def]
        assert path == "/agent/action"
        assert json is not None
        assert "Rank these items" in json["user_prompt"]
        assert json["output_type"] == "list[str]"
        return {
            "output": ["B", "A"],
            "message_history": [],
            "usage": {},
            "duration": 0.0,
        }

    monkeypatch.setattr(TracecatClient, "post", fake_post, raising=True)

    items: list[ai_ranker.RankableItem] = [
        {"id": "A", "text": "alpha"},
        {"id": "B", "text": "beta"},
    ]

    ranked = await ai_ranker.rank_items(
        items=items,
        criteria_prompt="return list as-is",
        model_name="gpt-4o-mini",
        model_provider="openai",
    )
    assert ranked == ["B", "A"]


@pytest.mark.anyio
async def test_rank_items_pairwise_filters_hallucinated_ids(
    registry_context: RegistryContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    call_count = 0

    async def fake_post(self: TracecatClient, path: str, *, params=None, json=None):  # type: ignore[no-untyped-def]
        nonlocal call_count
        call_count += 1
        assert path == "/agent/action"
        assert json is not None
        # Always return a hallucinated id plus the real ids in reverse order
        return {
            "output": ["ghost", "beta", "alpha"],
            "message_history": [],
            "usage": {},
            "duration": 0.0,
        }

    monkeypatch.setattr(TracecatClient, "post", fake_post, raising=True)
    monkeypatch.setattr(
        ai_ranker.random, "sample", lambda seq, k: list(seq)
    )  # deterministic

    items = cast(
        list[ai_ranker.RankableItem],
        [
            {"id": "alpha", "text": "first"},
            {"id": "beta", "text": "second"},
        ],
    )

    ranked = await ai_ranker.rank_items_pairwise(
        items=items,
        criteria_prompt="return hallucination first",
        model_name="gpt-4o-mini",
        model_provider="openai",
        batch_size=2,
        num_passes=1,
        refinement_ratio=0.0,
        max_requests=1,
    )

    assert ranked == ["beta", "alpha"]
    assert call_count >= 1
