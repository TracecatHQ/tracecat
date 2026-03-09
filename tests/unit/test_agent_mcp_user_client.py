from __future__ import annotations

from types import SimpleNamespace

import pytest

from tracecat.agent.mcp import user_client


def _set_transport_factories(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        user_client,
        "_create_streamable_http_transport",
        lambda url, headers=None, timeout=None: "streamable-http",
    )
    monkeypatch.setattr(
        user_client,
        "_create_sse_transport",
        lambda url, headers=None, timeout=None: "sse",
    )


@pytest.mark.anyio
async def test_discover_user_mcp_tools_falls_back_to_sse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_transport_factories(monkeypatch)
    attempts: list[tuple[str, str]] = []

    class FakeClient:
        def __init__(self, transport: str) -> None:
            self.transport = transport

        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def list_tools(self) -> list[SimpleNamespace]:
            attempts.append(("list_tools", self.transport))
            if self.transport == "streamable-http":
                raise RuntimeError("streamable-http unsupported")
            return [
                SimpleNamespace(
                    name="lookup",
                    description="Lookup",
                    inputSchema={"type": "object"},
                )
            ]

    monkeypatch.setattr(user_client, "Client", FakeClient)

    tools = await user_client.discover_user_mcp_tools(
        [{"name": "jira", "url": "https://example.com/mcp"}]
    )

    assert attempts == [
        ("list_tools", "streamable-http"),
        ("list_tools", "sse"),
    ]
    assert "mcp__jira__lookup" in tools


@pytest.mark.anyio
async def test_call_user_mcp_tool_prefers_streamable_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_transport_factories(monkeypatch)
    attempts: list[tuple[str, str]] = []

    class FakeClient:
        def __init__(self, transport: str) -> None:
            self.transport = transport

        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def list_tools(self) -> list[SimpleNamespace]:
            attempts.append(("list_tools", self.transport))
            return []

        async def call_tool(
            self, tool_name: str, args: dict[str, object]
        ) -> SimpleNamespace:
            attempts.append(("call_tool", self.transport))
            assert tool_name == "getIssue"
            assert args == {"id": 1}
            return SimpleNamespace(content=[SimpleNamespace(text="ok")])

    monkeypatch.setattr(user_client, "Client", FakeClient)

    result = await user_client.call_user_mcp_tool(
        [{"name": "jira", "url": "https://example.com/mcp"}],
        "jira",
        "getIssue",
        {"id": 1},
    )

    assert result == "ok"
    assert attempts == [
        ("list_tools", "streamable-http"),
        ("call_tool", "streamable-http"),
    ]


@pytest.mark.anyio
async def test_call_user_mcp_tool_falls_back_to_sse_when_probe_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_transport_factories(monkeypatch)
    attempts: list[tuple[str, str]] = []

    class FakeClient:
        def __init__(self, transport: str) -> None:
            self.transport = transport

        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def list_tools(self) -> list[SimpleNamespace]:
            attempts.append(("list_tools", self.transport))
            if self.transport == "streamable-http":
                raise RuntimeError("streamable-http unsupported")
            return []

        async def call_tool(
            self, tool_name: str, args: dict[str, object]
        ) -> SimpleNamespace:
            attempts.append(("call_tool", self.transport))
            assert tool_name == "getIssue"
            assert args == {"id": 1}
            return SimpleNamespace(content=[SimpleNamespace(text="fallback-ok")])

    monkeypatch.setattr(user_client, "Client", FakeClient)

    result = await user_client.call_user_mcp_tool(
        [{"name": "jira", "url": "https://example.com/mcp"}],
        "jira",
        "getIssue",
        {"id": 1},
    )

    assert result == "fallback-ok"
    assert attempts == [
        ("list_tools", "streamable-http"),
        ("list_tools", "sse"),
        ("call_tool", "sse"),
    ]


@pytest.mark.anyio
async def test_call_user_mcp_tool_falls_back_to_sse_when_connect_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_transport_factories(monkeypatch)
    attempts: list[tuple[str, str]] = []

    class FakeClient:
        def __init__(self, transport: str) -> None:
            self.transport = transport

        async def __aenter__(self) -> FakeClient:
            attempts.append(("enter", self.transport))
            if self.transport == "streamable-http":
                raise RuntimeError("streamable-http connect failed")
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def list_tools(self) -> list[SimpleNamespace]:
            attempts.append(("list_tools", self.transport))
            return []

        async def call_tool(
            self, tool_name: str, args: dict[str, object]
        ) -> SimpleNamespace:
            attempts.append(("call_tool", self.transport))
            assert tool_name == "getIssue"
            assert args == {"id": 1}
            return SimpleNamespace(content=[SimpleNamespace(text="fallback-ok")])

    monkeypatch.setattr(user_client, "Client", FakeClient)

    result = await user_client.call_user_mcp_tool(
        [{"name": "jira", "url": "https://example.com/mcp"}],
        "jira",
        "getIssue",
        {"id": 1},
    )

    assert result == "fallback-ok"
    assert attempts == [
        ("enter", "streamable-http"),
        ("enter", "sse"),
        ("list_tools", "sse"),
        ("call_tool", "sse"),
    ]


@pytest.mark.anyio
async def test_call_user_mcp_tool_does_not_retry_after_tool_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_transport_factories(monkeypatch)
    attempts: list[tuple[str, str]] = []

    class FakeClient:
        def __init__(self, transport: str) -> None:
            self.transport = transport

        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def list_tools(self) -> list[SimpleNamespace]:
            attempts.append(("list_tools", self.transport))
            return []

        async def call_tool(
            self, tool_name: str, args: dict[str, object]
        ) -> SimpleNamespace:
            attempts.append(("call_tool", self.transport))
            raise RuntimeError("tool failed")

    monkeypatch.setattr(user_client, "Client", FakeClient)

    with pytest.raises(RuntimeError, match="tool failed"):
        await user_client.call_user_mcp_tool(
            [{"name": "jira", "url": "https://example.com/mcp"}],
            "jira",
            "getIssue",
            {"id": 1},
        )

    assert attempts == [
        ("list_tools", "streamable-http"),
        ("call_tool", "streamable-http"),
    ]
