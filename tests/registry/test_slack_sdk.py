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
