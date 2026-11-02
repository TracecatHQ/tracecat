"""Stdio MCP runner loading through the ``tracecat.stdio_mcp_runner`` group."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from importlib.metadata import EntryPoint
from typing import Any
from unittest.mock import patch

import pytest
from mcp.types import CallToolResult, TextContent

from tracecat.agent.mcp import stdio_runner
from tracecat.agent.mcp.stdio_probe import NsjailStdioMCPRunner
from tracecat.agent.mcp.stdio_probe_types import StdioMCPProbeResult
from tracecat.agent.mcp.stdio_runner import (
    STDIO_MCP_RUNNER_ENTRY_POINT_GROUP,
    StdioMCPRunnerUnsupportedError,
    StdioMCPServer,
    get_stdio_mcp_runner,
)


class FakeRunner:
    async def list_tools(
        self, server: StdioMCPServer, env: dict[str, str] | None
    ) -> StdioMCPProbeResult:
        del server, env
        return StdioMCPProbeResult(success=True, message="ok")

    async def call_tool(
        self,
        server: StdioMCPServer,
        env: dict[str, str] | None,
        tool: str,
        args: dict[str, Any],
    ) -> CallToolResult:
        del server, env, tool, args
        return CallToolResult(content=[TextContent(type="text", text="ok")])


class NotARunner:
    async def list_tools(self) -> None:
        return None


def _entry_point(name: str, attribute: str) -> EntryPoint:
    return EntryPoint(
        name=name,
        value=f"{__name__}:{attribute}",
        group=STDIO_MCP_RUNNER_ENTRY_POINT_GROUP,
    )


@pytest.fixture(autouse=True)
def clean_runner_cache() -> Iterator[None]:
    get_stdio_mcp_runner.cache_clear()
    yield
    get_stdio_mcp_runner.cache_clear()


def _installed(*entries: EntryPoint):
    def fake_entry_points(*, group: str) -> list[EntryPoint]:
        assert group == STDIO_MCP_RUNNER_ENTRY_POINT_GROUP
        return list(entries)

    return patch.object(stdio_runner, "entry_points", fake_entry_points)


def test_builtin_runner_is_the_default() -> None:
    with _installed():
        assert isinstance(get_stdio_mcp_runner(), NsjailStdioMCPRunner)


def test_installed_runner_replaces_the_builtin() -> None:
    with _installed(_entry_point("external", "FakeRunner")):
        assert isinstance(get_stdio_mcp_runner(), FakeRunner)


def test_two_installed_runners_are_rejected() -> None:
    with (
        _installed(_entry_point("a", "FakeRunner"), _entry_point("b", "FakeRunner")),
        pytest.raises(ValueError, match="Multiple stdio MCP runners"),
    ):
        get_stdio_mcp_runner()


def test_runner_missing_methods_is_rejected() -> None:
    with (
        _installed(_entry_point("external", "NotARunner")),
        pytest.raises(TypeError, match="Invalid stdio MCP runner contract"),
    ):
        get_stdio_mcp_runner()


@pytest.mark.anyio
async def test_builtin_runner_refuses_tool_calls() -> None:
    server = StdioMCPServer(
        workspace_id=uuid.uuid4(),
        mcp_integration_id=uuid.uuid4(),
        name="files",
        command="npx",
    )
    with pytest.raises(StdioMCPRunnerUnsupportedError):
        await NsjailStdioMCPRunner().call_tool(server, None, "read", {})
