from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from tracecat_registry.integrations import slack_sdk


@pytest.mark.anyio
async def test_call_method_uses_sdk_method_when_available(monkeypatch) -> None:
    client = SimpleNamespace(
        chat_postMessage=AsyncMock(return_value=SimpleNamespace(data={"ok": True})),
        api_call=AsyncMock(),
    )

    monkeypatch.setattr(slack_sdk.secrets, "get", lambda _key: "xoxb-token")
    monkeypatch.setattr(slack_sdk, "AsyncWebClient", lambda token: client)

    result = await slack_sdk.call_method(
        sdk_method="chat_postMessage",
        params={"channel": "C123", "text": "hello"},
    )

    assert result == {"ok": True}
    client.chat_postMessage.assert_awaited_once_with(channel="C123", text="hello")
    client.api_call.assert_not_awaited()


@pytest.mark.anyio
async def test_call_method_falls_back_to_raw_web_api_method(monkeypatch) -> None:
    client = SimpleNamespace(
        api_call=AsyncMock(return_value=SimpleNamespace(data={"ok": True})),
    )

    monkeypatch.setattr(slack_sdk.secrets, "get", lambda _key: "xoxb-token")
    monkeypatch.setattr(slack_sdk, "AsyncWebClient", lambda token: client)

    result = await slack_sdk.call_method(
        sdk_method="assistant.threads.setStatus",
        params={
            "channel_id": "C123",
            "thread_ts": "1700000000.001",
            "status": "is thinking...",
        },
    )

    assert result == {"ok": True}
    client.api_call.assert_awaited_once_with(
        api_method="assistant.threads.setStatus",
        json={
            "channel_id": "C123",
            "thread_ts": "1700000000.001",
            "status": "is thinking...",
        },
    )


class FakePaginator:
    """Mimics an awaited AsyncSlackResponse that yields one response per page."""

    def __init__(self, pages: list[dict]) -> None:
        self._pages = pages
        self.pages_fetched = 0

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for page_data in self._pages:
            self.pages_fetched += 1
            yield SimpleNamespace(data=page_data)


@pytest.mark.anyio
async def test_call_paginated_method_stops_at_limit(monkeypatch) -> None:
    paginator = FakePaginator(
        [
            {"messages": [{"ts": "1"}, {"ts": "2"}]},
            {"messages": [{"ts": "3"}, {"ts": "4"}]},
            {"messages": [{"ts": "5"}, {"ts": "6"}]},
        ]
    )
    client = SimpleNamespace(
        conversations_history=AsyncMock(return_value=paginator),
    )

    monkeypatch.setattr(slack_sdk.secrets, "get", lambda _key: "xoxb-token")
    monkeypatch.setattr(slack_sdk, "AsyncWebClient", lambda token: client)

    result = await slack_sdk.call_paginated_method(
        sdk_method="conversations_history",
        params={"channel": "C123"},
        key="messages",
        limit=3,
    )

    assert result == [{"ts": "1"}, {"ts": "2"}, {"ts": "3"}]
    # Pagination must stop once the limit is reached, not exhaust all pages.
    assert paginator.pages_fetched == 2
    client.conversations_history.assert_awaited_once_with(channel="C123", limit=3)


@pytest.mark.anyio
async def test_call_paginated_method_returns_all_items_below_limit(
    monkeypatch,
) -> None:
    paginator = FakePaginator(
        [
            {"members": ["U1", "U2"]},
            {"members": ["U3"]},
        ]
    )
    client = SimpleNamespace(
        conversations_members=AsyncMock(return_value=paginator),
    )

    monkeypatch.setattr(slack_sdk.secrets, "get", lambda _key: "xoxb-token")
    monkeypatch.setattr(slack_sdk, "AsyncWebClient", lambda token: client)

    result = await slack_sdk.call_paginated_method(
        sdk_method="conversations_members",
        params={"channel": "C123"},
        key="members",
        limit=10,
    )

    assert result == ["U1", "U2", "U3"]
    assert paginator.pages_fetched == 2


@pytest.mark.anyio
async def test_call_paginated_method_without_key_caps_pages(monkeypatch) -> None:
    paginator = FakePaginator(
        [
            {"ok": True, "page": 1},
            {"ok": True, "page": 2},
            {"ok": True, "page": 3},
        ]
    )
    client = SimpleNamespace(
        conversations_history=AsyncMock(return_value=paginator),
    )

    monkeypatch.setattr(slack_sdk.secrets, "get", lambda _key: "xoxb-token")
    monkeypatch.setattr(slack_sdk, "AsyncWebClient", lambda token: client)

    result = await slack_sdk.call_paginated_method(
        sdk_method="conversations_history",
        params={"channel": "C123"},
        limit=2,
    )

    assert result == [{"ok": True, "page": 1}, {"ok": True, "page": 2}]
    assert paginator.pages_fetched == 2


@pytest.mark.anyio
async def test_call_paginated_method_raises_on_missing_key(monkeypatch) -> None:
    paginator = FakePaginator([{"members": ["U1"]}])
    client = SimpleNamespace(
        conversations_history=AsyncMock(return_value=paginator),
    )

    monkeypatch.setattr(slack_sdk.secrets, "get", lambda _key: "xoxb-token")
    monkeypatch.setattr(slack_sdk, "AsyncWebClient", lambda token: client)

    with pytest.raises(ValueError, match="not found in data"):
        await slack_sdk.call_paginated_method(
            sdk_method="conversations_history",
            params={"channel": "C123"},
            key="messages",
            limit=10,
        )
