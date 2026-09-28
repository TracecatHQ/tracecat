"""Docker-backed smoke tests for stdio MCP probing through nsjail."""

from __future__ import annotations

import os
import shutil

import pytest

from tests.conftest import IN_DOCKER
from tracecat.agent.mcp.stdio_probe import probe_stdio_mcp_tools_in_sandbox

NSJAIL_AVAILABLE = shutil.which("nsjail") is not None
ROOTFS_PATH = os.environ.get(
    "TRACECAT__SANDBOX_ROOTFS_PATH", "/var/lib/tracecat/sandbox-rootfs"
)
ROOTFS_AVAILABLE = os.path.isdir(ROOTFS_PATH)
SERVER_ENV = {"PYTHONPATH": "/pythonpath/0"}

pytestmark = pytest.mark.skipif(
    not (IN_DOCKER and NSJAIL_AVAILABLE and ROOTFS_AVAILABLE),
    reason=(
        "nsjail sandbox smoke requires Docker with nsjail and rootfs. "
        f"in Docker: {IN_DOCKER}, "
        f"nsjail installed: {NSJAIL_AVAILABLE}, "
        f"rootfs exists: {ROOTFS_AVAILABLE}"
    ),
)

FAKE_MCP_SERVER = '''
from fastmcp import FastMCP

mcp = FastMCP("probe-test")


@mcp.tool
def echo(text: str) -> str:
    """Echo the provided text."""
    return text


@mcp.tool
def list_alerts() -> list[str]:
    """List alert identifiers."""
    return ["alert-1"]


mcp.run()
'''


@pytest.mark.anyio
async def test_probe_nsjail_probes_real_stdio_mcp_server() -> None:
    """End-to-end: nsjail launches a real FastMCP stdio server."""
    result = await probe_stdio_mcp_tools_in_sandbox(
        command="/usr/local/bin/python3",
        args=["-c", FAKE_MCP_SERVER],
        env=SERVER_ENV,
        timeout=30,
    )

    assert result.error is None
    assert result.success is True
    tool_by_name = {tool.name: tool for tool in result.tools}
    assert set(tool_by_name) == {"echo", "list_alerts"}
    assert tool_by_name["echo"].description == "Echo the provided text."
    assert tool_by_name["list_alerts"].description == "List alert identifiers."


@pytest.mark.anyio
async def test_probe_nsjail_reports_launch_failure() -> None:
    """A bad stdio command yields a clean failure result through nsjail."""
    result = await probe_stdio_mcp_tools_in_sandbox(
        command="/usr/local/bin/python3",
        args=["-c", "import sys; sys.exit(3)"],
        env=SERVER_ENV,
        timeout=10,
    )

    assert result.success is False
    assert result.tools == []
    assert result.error is not None
