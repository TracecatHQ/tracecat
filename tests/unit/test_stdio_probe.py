"""Tests for sandboxed stdio MCP probing."""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tracecat.agent.mcp.stdio_probe import probe_stdio_mcp_tools_in_sandbox

FAKE_MCP_SERVER = '''
from fastmcp import FastMCP

mcp = FastMCP("probe-test")


@mcp.tool
def echo(text: str) -> str:
    """Echo the provided text."""
    return text


mcp.run()
'''


@pytest.mark.anyio
async def test_probe_falls_back_to_pid_isolation_without_nsjail() -> None:
    """Without nsjail, the probe runs under the best-effort PID fallback."""
    with (
        patch(
            "tracecat.agent.mcp.stdio_probe.is_nsjail_available",
            return_value=False,
        ),
        patch("tracecat.agent.mcp.stdio_probe.NsjailExecutor") as executor_cls,
        patch(
            "tracecat.agent.mcp.stdio_probe._execute_probe_without_nsjail",
            new_callable=AsyncMock,
        ) as fallback,
    ):
        fallback.return_value = MagicMock(
            success=True,
            output={"tools": [{"name": "echo", "description": "Echo"}]},
            error=None,
            stderr="",
        )
        result = await probe_stdio_mcp_tools_in_sandbox(
            command="python",
            args=["-m", "example"],
            env=None,
            timeout=5,
        )

    assert result.success is True
    assert [tool.name for tool in result.tools] == ["echo"]
    executor_cls.assert_not_called()
    fallback.assert_awaited_once()


@pytest.mark.anyio
async def test_probe_runs_in_sandbox_when_nsjail_available() -> None:
    """With nsjail available, the probe executes inside the sandbox."""
    sandbox_result = MagicMock(
        success=True,
        output={
            "tools": [
                {"name": "list_alerts", "description": "List alerts"},
            ]
        },
        error=None,
        stderr="",
    )
    executor = MagicMock(execute=AsyncMock(return_value=sandbox_result))

    with (
        patch(
            "tracecat.agent.mcp.stdio_probe.is_nsjail_available",
            return_value=True,
        ),
        patch(
            "tracecat.agent.mcp.stdio_probe.NsjailExecutor",
            return_value=executor,
        ),
    ):
        result = await probe_stdio_mcp_tools_in_sandbox(
            command="python",
            args=["-m", "example"],
            env=None,
            timeout=5,
        )

    assert result.success is True
    assert [tool.name for tool in result.tools] == ["list_alerts"]
    executor.execute.assert_awaited_once()


@pytest.mark.anyio
async def test_probe_filters_unsupported_stdio_tool_names() -> None:
    """Probe results only persist tool names the runtime can expose."""
    with (
        patch(
            "tracecat.agent.mcp.stdio_probe.is_nsjail_available",
            return_value=False,
        ),
        patch(
            "tracecat.agent.mcp.stdio_probe._execute_probe_without_nsjail",
            new_callable=AsyncMock,
        ) as fallback,
    ):
        fallback.return_value = MagicMock(
            success=True,
            output={
                "tools": [
                    {"name": "list_alerts", "description": "List alerts"},
                    {"name": "issue.get", "description": "Unsupported dotted name"},
                    {"name": "search-repos", "description": "Search repos"},
                    {"name": "x" * 129, "description": "Too long"},
                ]
            },
            error=None,
            stderr="",
        )
        result = await probe_stdio_mcp_tools_in_sandbox(
            command="python",
            args=["-m", "example"],
            env=None,
            timeout=5,
        )

    assert result.success is True
    assert [tool.name for tool in result.tools] == ["list_alerts", "search-repos"]


@pytest.mark.anyio
async def test_pid_fallback_probes_real_stdio_mcp_server(tmp_path: Path) -> None:
    """End-to-end: the PID fallback launches a real fastmcp stdio server."""
    server_path = tmp_path / "server.py"
    server_path.write_text(FAKE_MCP_SERVER, encoding="utf-8")

    with (
        patch(
            "tracecat.agent.mcp.stdio_probe.is_nsjail_available",
            return_value=False,
        ),
        # Force the plain-subprocess path so the test behaves the same on
        # hosts with and without unshare.
        patch(
            "tracecat.agent.mcp.stdio_probe.pid_namespace_available",
            new_callable=AsyncMock,
            return_value=False,
        ),
    ):
        result = await probe_stdio_mcp_tools_in_sandbox(
            command=sys.executable,
            args=[str(server_path)],
            env=None,
            timeout=30,
        )

    assert result.error is None
    assert result.success is True
    assert [tool.name for tool in result.tools] == ["echo"]
    assert result.tools[0].description == "Echo the provided text."


@pytest.mark.anyio
async def test_pid_fallback_reports_launch_failure(tmp_path: Path) -> None:
    """A command that is not an MCP server yields a clean failure result."""
    with (
        patch(
            "tracecat.agent.mcp.stdio_probe.is_nsjail_available",
            return_value=False,
        ),
        patch(
            "tracecat.agent.mcp.stdio_probe.pid_namespace_available",
            new_callable=AsyncMock,
            return_value=False,
        ),
    ):
        result = await probe_stdio_mcp_tools_in_sandbox(
            command=sys.executable,
            args=["-c", "import sys; sys.exit(3)"],
            env=None,
            timeout=10,
        )

    assert result.success is False
    assert result.tools == []
    assert result.error is not None
