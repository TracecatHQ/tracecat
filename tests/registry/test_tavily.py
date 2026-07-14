from typing import Any

import pytest
from tracecat_registry.integrations import tavily


class FakeAsyncTavilyClient:
    """Records the kwargs that web_search forwards to the Tavily SDK."""

    last_search_kwargs: dict[str, Any] = {}

    def __init__(self, *, api_key: str) -> None:
        self.api_key = api_key

    async def search(self, query: str, **kwargs: Any) -> dict[str, Any]:
        type(self).last_search_kwargs = {"query": query, **kwargs}
        return {"results": [], "query": query}


@pytest.fixture(autouse=True)
def patch_tavily(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeAsyncTavilyClient.last_search_kwargs = {}
    monkeypatch.setattr(tavily, "AsyncTavilyClient", FakeAsyncTavilyClient)
    monkeypatch.setattr(
        tavily.secrets,
        "get",
        lambda key: "tavily-key" if key == "TAVILY_API_KEY" else None,
    )


@pytest.mark.anyio
async def test_web_search_forwards_search_depth_to_sdk() -> None:
    """The SDK kwarg is `search_depth`; passing anything else lands in the
    client's **kwargs and the requested depth is silently ignored."""
    result = await tavily.web_search(
        query="tracecat",
        search_depth="advanced",
        topic="news",
        time_range="week",
    )

    assert result == {"results": [], "query": "tracecat"}
    assert FakeAsyncTavilyClient.last_search_kwargs == {
        "query": "tracecat",
        "search_depth": "advanced",
        "topic": "news",
        "time_range": "week",
    }
