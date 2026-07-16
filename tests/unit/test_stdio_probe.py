"""Tests for sandboxed stdio MCP probing."""

import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tracecat.agent.mcp.stdio_probe import (
    MCP_STDIO_PROBE_HARD_TIMEOUT_BUFFER,
    MCP_STDIO_PROBE_TIMEOUT,
    build_stdio_mcp_probe_workflow_id,
    probe_stdio_mcp_tools_in_sandbox,
    sanitize_stdio_probe_error,
)
from tracecat.sandbox.exceptions import SandboxTimeoutError
from tracecat.sandbox.types import SandboxErrorCode, SandboxResult

FAKE_MCP_SERVER = '''
from fastmcp import FastMCP

mcp = FastMCP("probe-test")


@mcp.tool
def echo(text: str) -> str:
    """Echo the provided text."""
    return text


mcp.run()
'''


def test_build_stdio_mcp_probe_workflow_id_is_workspace_scoped() -> None:
    workspace_id = uuid.uuid4()
    mcp_integration_id = uuid.uuid4()

    assert (
        build_stdio_mcp_probe_workflow_id(
            workspace_id=workspace_id,
            mcp_integration_id=mcp_integration_id,
        )
        == f"mcp-stdio-probe/ws/{workspace_id}/{mcp_integration_id}"
    )


def test_sanitize_stdio_probe_error_preserves_verbose_diagnostics() -> None:
    """User-facing probe errors retain verbose installer and server output."""
    error = """McpError: Connection closed
Downloading cryptography (4.5MiB)
 Downloaded cryptography
Installed 91 packages in 26ms
AuthlibDeprecationWarning: authlib.jose module is deprecated
╭──────────────────────────────────────────────────────────────────────────────╮
│ FastMCP 2.14.7                                                               │
╰──────────────────────────────────────────────────────────────────────────────╯
Authentication failed: invalid API token
"""

    sanitized = sanitize_stdio_probe_error(error)

    assert sanitized == error.strip()
    assert "Downloading" in sanitized
    assert "FastMCP" in sanitized


def test_sanitize_stdio_probe_error_redacts_common_sensitive_values() -> None:
    """Verbose diagnostics redact configured and commonly formatted secrets."""
    error = """Request failed for https://user:pass@example.com/path?token=query
User: analyst@example.com
Authorization: Bearer bearer-value
api_key=inline-value
"client_secret": "json-value"
password: "correct horse battery staple"
secret: 'synthetic phrase with spaces'
Set-Cookie: session=synthetic-cookie
JWT: eyJhbGciOiJIUzI1NiJ9.payload.signature
Configured token: configured-value
"""

    sanitized = sanitize_stdio_probe_error(
        error,
        env={"PANTHER_API_TOKEN": "configured-value"},
    )

    assert "user:pass" not in sanitized
    assert "?token=query" not in sanitized
    assert "analyst@example.com" not in sanitized
    assert "bearer-value" not in sanitized
    assert "inline-value" not in sanitized
    assert "json-value" not in sanitized
    assert "correct horse battery staple" not in sanitized
    assert "synthetic phrase with spaces" not in sanitized
    assert "synthetic-cookie" not in sanitized
    assert "eyJhbGciOiJIUzI1NiJ9.payload.signature" not in sanitized
    assert "configured-value" not in sanitized
    assert "[redacted email]" in sanitized
    assert "Authorization: [redacted]" in sanitized
    assert '"client_secret": "[redacted]"' in sanitized
    assert 'password: "[redacted]"' in sanitized
    assert "secret: '[redacted]'" in sanitized
    assert "Set-Cookie: [redacted]" in sanitized


def test_sanitize_stdio_probe_error_sanitizes_url_before_env_replacement() -> None:
    """A query secret appended to an env base URL cannot escape redaction."""
    error = "Request failed: https://example.test/path?signature=synthetic-secret"

    sanitized = sanitize_stdio_probe_error(
        error,
        env={"BASE_URL": "https://example.test/path"},
    )

    assert sanitized == "Request failed: [redacted]"
    assert "synthetic-secret" not in sanitized


def test_sanitize_stdio_probe_error_explains_bare_connection_close() -> None:
    """A server that exits silently still produces an actionable error."""
    assert sanitize_stdio_probe_error("McpError: Connection closed") == (
        "The MCP server process closed before verification completed. "
        "Check the server URL and credentials, then try again."
    )


def test_sanitize_stdio_probe_error_keeps_tail_of_long_error() -> None:
    """Late server failures survive bounded error output."""
    sanitized = sanitize_stdio_probe_error(
        f"McpError: Connection closed\n{'x' * 15_000}\nInvalid credentials"
    )

    assert sanitized.startswith("McpError: Connection closed\n")
    assert "… output truncated …" in sanitized
    assert sanitized.endswith("Invalid credentials")
    assert len(sanitized) == 12_000


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
async def test_probe_timeout_leaves_buffer_before_nsjail_limit() -> None:
    """The probe can write a structured result before nsjail hard-stops it."""
    sandbox_result = MagicMock(
        success=True,
        output={"tools": []},
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
        await probe_stdio_mcp_tools_in_sandbox(
            command="python",
            args=["-m", "example"],
            env=None,
            timeout=MCP_STDIO_PROBE_TIMEOUT,
        )

    config = executor.execute.await_args.args[1]
    assert config.resources.cpu_seconds == (
        MCP_STDIO_PROBE_TIMEOUT + MCP_STDIO_PROBE_HARD_TIMEOUT_BUFFER
    )
    assert config.resources.timeout_seconds == (
        MCP_STDIO_PROBE_TIMEOUT + MCP_STDIO_PROBE_HARD_TIMEOUT_BUFFER
    )


@pytest.mark.anyio
async def test_probe_returns_friendly_structured_timeout() -> None:
    """A graceful in-sandbox timeout produces an actionable user error."""
    executor = MagicMock(
        execute=AsyncMock(
            return_value=SandboxResult(
                success=False,
                error_code=SandboxErrorCode.TIMEOUT,
                error=None,
                stderr="",
            )
        )
    )

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
            timeout=45,
        )

    assert result.success is False
    assert result.message == "MCP server verification timed out"
    assert result.error is not None
    assert "within 45 seconds" in result.error
    assert "network access" in result.error


@pytest.mark.anyio
async def test_probe_returns_friendly_sandbox_timeout() -> None:
    """An outer sandbox timeout uses the same actionable user error."""
    executor = MagicMock(
        execute=AsyncMock(side_effect=SandboxTimeoutError("internal detail"))
    )

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
            timeout=45,
        )

    assert result.success is False
    assert result.message == "MCP server verification timed out"
    assert result.error is not None
    assert "within 45 seconds" in result.error


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


@pytest.mark.anyio
async def test_pid_fallback_reports_friendly_timeout() -> None:
    """The real probe script reports a timeout without sandbox diagnostics."""
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
            args=["-c", "import time; time.sleep(10)"],
            env=None,
            timeout=1,
        )

    assert result.success is False
    assert result.message == "MCP server verification timed out"
    assert result.error is not None
    assert "within 1 second" in result.error
    assert "USERNS" not in result.error


@pytest.mark.anyio
async def test_pid_fallback_redacts_sensitive_server_stderr(tmp_path: Path) -> None:
    """The sandbox boundary keeps verbose stderr without returning secrets."""
    server_path = tmp_path / "failing_server.py"
    server_path.write_text(
        """import os
import sys

print("diagnostic: initializing synthetic server", file=sys.stderr)
print("user: analyst@example.com", file=sys.stderr)
print("Authorization: Bearer synthetic-bearer-value", file=sys.stderr)
print(f"token: {os.environ['SYNTHETIC_TOKEN']}", file=sys.stderr)
print(f"url: {os.environ['BASE_URL']}?signature=synthetic-query-secret", file=sys.stderr)
print('password: "synthetic phrase with spaces"', file=sys.stderr)
raise RuntimeError("synthetic startup failure")
""",
        encoding="utf-8",
    )

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
            args=[str(server_path)],
            env={
                "SYNTHETIC_TOKEN": "synthetic-configured-secret",
                "BASE_URL": "https://example.test/path",
            },
            timeout=10,
        )

    assert result.success is False
    assert result.error is not None
    assert "diagnostic: initializing synthetic server" in result.error
    assert "synthetic startup failure" in result.error
    assert "analyst@example.com" not in result.error
    assert "synthetic-bearer-value" not in result.error
    assert "synthetic-configured-secret" not in result.error
    assert "synthetic-query-secret" not in result.error
    assert "synthetic phrase with spaces" not in result.error
    assert "[redacted email]" in result.error
    assert 'password: "[redacted]"' in result.error
