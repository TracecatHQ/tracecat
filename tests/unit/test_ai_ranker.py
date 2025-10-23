from __future__ import annotations

from typing import Any, cast

import pytest

from tracecat.ai import ranker

requires_openai_mocks = pytest.mark.usefixtures("mock_openai_secrets")


class DummyAgentOutput:
    """Lightweight stand-in for AgentOutput with configurable payload."""

    def __init__(self, output: Any):
        self._output = output

    def model_dump(self) -> dict[str, Any]:
        return {"output": self._output}


def _build_fake_agent() -> object:
    return object()


@pytest.mark.anyio
async def test_rank_items_returns_empty_list_without_building_agent(
    monkeypatch: pytest.MonkeyPatch,
):
    called = False

    async def fake_build_agent(config: Any) -> object:
        nonlocal called
        called = True
        return _build_fake_agent()

    monkeypatch.setattr(ranker, "build_agent", fake_build_agent)

    result = await ranker.rank_items(
        items=[],
        criteria_prompt="anything",
        model_name="gpt-4o-mini",
        model_provider="openai",
    )

    assert result == []
    assert called is False


@pytest.mark.anyio
async def test_rank_items_missing_identifier_raises_value_error() -> None:
    with pytest.raises(ValueError, match="missing required field 'id'"):
        await ranker.rank_items(
            items=[{"text": "Sample"}],
            criteria_prompt="rank by relevance",
            model_name="gpt-4o-mini",
            model_provider="openai",
        )


@pytest.mark.anyio
async def test_rank_items_parses_markdown_wrapped_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_build_agent(config: Any) -> object:
        return _build_fake_agent()

    async def fake_run_agent_sync(
        agent: Any,
        user_prompt: str,
        *,
        max_requests: int,
    ) -> DummyAgentOutput:
        return DummyAgentOutput('```json\n["A", "B"]\n```')

    monkeypatch.setattr(ranker, "build_agent", fake_build_agent)
    monkeypatch.setattr(ranker, "run_agent_sync", fake_run_agent_sync)

    items = [
        {"id": "A", "text": "Critical issue"},
        {"id": "B", "text": "Minor bug"},
    ]

    ranked = await ranker.rank_items(
        items=items,
        criteria_prompt="rank by severity descending",
        model_name="gpt-4o-mini",
        model_provider="openai",
    )

    assert ranked == ["A", "B"]


@pytest.mark.anyio
async def test_rank_items_accepts_already_parsed_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_build_agent(config: Any) -> object:
        return _build_fake_agent()

    async def fake_run_agent_sync(
        agent: Any,
        user_prompt: str,
        *,
        max_requests: int,
    ) -> DummyAgentOutput:
        return DummyAgentOutput(["B", "A"])

    monkeypatch.setattr(ranker, "build_agent", fake_build_agent)
    monkeypatch.setattr(ranker, "run_agent_sync", fake_run_agent_sync)

    items = [{"id": "A", "text": "alpha"}, {"id": "B", "text": "beta"}]

    ranked = await ranker.rank_items(
        items=items,
        criteria_prompt="return list as-is",
        model_name="gpt-4o-mini",
        model_provider="openai",
    )

    assert ranked == ["B", "A"]


def test_shuffle_items_uses_random_shuffle(monkeypatch: pytest.MonkeyPatch) -> None:
    initial = cast(
        list[ranker.RankableItem],
        [
            {"id": 1, "text": "one"},
            {"id": 2, "text": "two"},
            {"id": 3, "text": "three"},
        ],
    )

    def fake_shuffle(seq: list[ranker.RankableItem]) -> None:
        seq.reverse()

    monkeypatch.setattr(ranker.random, "shuffle", fake_shuffle)

    shuffled_result = ranker._shuffle_items(initial)

    assert shuffled_result is not initial
    assert [item["id"] for item in shuffled_result] == [3, 2, 1]


def test_create_batches_chunks_items_evenly() -> None:
    items = cast(
        list[ranker.RankableItem],
        [{"id": idx, "text": str(idx)} for idx in range(5)],
    )
    batches = ranker._create_batches(items, batch_size=2)

    assert len(batches) == 3
    assert [len(batch) for batch in batches] == [2, 2, 1]
    assert batches[0][0]["id"] == 0
    assert batches[-1][-1]["id"] == 4


def test_average_scores_returns_mean_values() -> None:
    scores = cast(dict[str | int, list[float]], {"a": [0.0, 1.0], "b": [1.0, 3.0]})
    averages = ranker._average_scores(scores)

    assert averages == {"a": 0.5, "b": 2.0}


@pytest.mark.anyio
async def test_rank_batch_formats_prompt_and_parses_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_prompt: dict[str, Any] = {}

    async def fake_run_agent_sync(
        agent: Any,
        user_prompt: str,
        *,
        max_requests: int,
    ) -> DummyAgentOutput:
        captured_prompt["value"] = user_prompt
        return DummyAgentOutput('["item-1", "item-2"]')

    monkeypatch.setattr(ranker, "run_agent_sync", fake_run_agent_sync)

    batch = cast(
        list[ranker.RankableItem],
        [
            {"id": "item-1", "text": "Alpha vulnerability"},
            {"id": "item-2", "text": "", "detail": "Fallback text path"},
        ],
    )

    ranked = await ranker._rank_batch(
        batch=batch,
        criteria_prompt="rank alphabetically",
        id_field="id",
        agent=_build_fake_agent(),
        max_requests=3,
    )

    assert ranked == ["item-1", "item-2"]
    prompt = captured_prompt["value"]
    assert "Alpha vulnerability" in prompt
    assert '"detail": "Fallback text path"' in prompt


@pytest.mark.anyio
async def test_rank_batch_accepts_already_parsed_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_run_agent_sync(
        agent: Any,
        user_prompt: str,
        *,
        max_requests: int,
    ) -> DummyAgentOutput:
        return DummyAgentOutput(["item-2", "item-1"])

    monkeypatch.setattr(ranker, "run_agent_sync", fake_run_agent_sync)

    batch = cast(
        list[ranker.RankableItem],
        [
            {"id": "item-1", "text": "Alpha"},
            {"id": "item-2", "text": "Beta"},
        ],
    )

    ranked = await ranker._rank_batch(
        batch=batch,
        criteria_prompt="reverse order",
        id_field="id",
        agent=_build_fake_agent(),
        max_requests=3,
    )

    assert ranked == ["item-2", "item-1"]


@pytest.mark.anyio
async def test_rank_batch_raises_for_non_list_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_run_agent_sync(
        agent: Any,
        user_prompt: str,
        *,
        max_requests: int,
    ) -> DummyAgentOutput:
        return DummyAgentOutput('{"unexpected": "format"}')

    monkeypatch.setattr(ranker, "run_agent_sync", fake_run_agent_sync)

    batch = cast(
        list[ranker.RankableItem],
        [{"id": "item-1", "text": "Only item"}],
    )

    with pytest.raises(ValueError, match="did not return a list"):
        await ranker._rank_batch(
            batch=batch,
            criteria_prompt="rank alphabetically",
            id_field="id",
            agent=_build_fake_agent(),
            max_requests=3,
        )


@pytest.mark.anyio
async def test_rank_items_pairwise_combines_refined_and_remaining(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_build_agent(config: Any) -> object:
        return _build_fake_agent()

    async def fake_rank_batch(
        *,
        batch: list[ranker.RankableItem],
        criteria_prompt: str,
        id_field: str,
        agent: Any,
        max_requests: int,
    ) -> list[str | int]:
        return [item[id_field] for item in batch]

    def identity_shuffle(items: list[ranker.RankableItem]) -> list[ranker.RankableItem]:
        return list(items)

    monkeypatch.setattr(ranker, "build_agent", fake_build_agent)
    monkeypatch.setattr(ranker, "_rank_batch", fake_rank_batch)
    monkeypatch.setattr(ranker, "_shuffle_items", identity_shuffle)

    items = cast(
        list[ranker.RankableItem],
        [
            {"id": "A", "text": "priority 4"},
            {"id": "B", "text": "priority 3"},
            {"id": "C", "text": "priority 2"},
            {"id": "D", "text": "priority 1"},
        ],
    )

    ranked = await ranker.rank_items_pairwise(
        items=items,
        criteria_prompt="no-op ordering",
        model_name="gpt-4o-mini",
        model_provider="openai",
        batch_size=2,
        num_passes=2,
        refinement_ratio=0.5,
    )

    assert ranked == ["A", "C", "B", "D"]


@pytest.mark.anyio
async def test_multi_pass_rank_handles_missing_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_rank_batch(
        *,
        batch: list[ranker.RankableItem],
        criteria_prompt: str,
        id_field: str,
        agent: Any,
        max_requests: int,
    ) -> list[str | int]:
        return [
            batch[0][id_field]
        ]  # Drop all but the first item to trigger missing-id handling

    def identity_shuffle(items: list[ranker.RankableItem]) -> list[ranker.RankableItem]:
        return list(items)

    monkeypatch.setattr(ranker, "_rank_batch", fake_rank_batch)
    monkeypatch.setattr(ranker, "_shuffle_items", identity_shuffle)

    items = cast(
        list[ranker.RankableItem],
        [
            {"id": "X", "text": "first"},
            {"id": "Y", "text": "second"},
            {"id": "Z", "text": "third"},
        ],
    )

    ranked = await ranker._multi_pass_rank(
        items=items,
        criteria_prompt="prefer the first item only",
        id_field="id",
        agent=_build_fake_agent(),
        batch_size=2,
        num_passes=1,
        refinement_ratio=0.0,
        max_requests=2,
    )

    assert ranked == ["X", "Z", "Y"]


@pytest.mark.anyio
@requires_openai_mocks
async def test_rank_items_live_openai() -> None:
    items = [
        {"id": "high", "text": "priority: 3 (highest)"},
        {"id": "medium", "text": "priority: 2"},
        {"id": "low", "text": "priority: 1 (lowest)"},
    ]

    ranked = await ranker.rank_items(
        items=items,
        criteria_prompt=(
            "Rank items by their numeric priority value from highest to lowest. "
            "Each item's text contains 'priority: N'. The correct ordering is the IDs "
            "whose priority numbers are sorted descending."
        ),
        model_name="gpt-5-nano-2025-08-07",
        model_provider="openai",
    )
    assert ranked == ["high", "medium", "low"]
