"""Sandboxed stdio MCP connection probing."""

from __future__ import annotations

import re
import sysconfig
import tempfile
import textwrap
import uuid
from pathlib import Path

import orjson
from pydantic import BaseModel, Field

from tracecat.auth.types import Role
from tracecat.integrations.schemas import MCPToolSummary
from tracecat.sandbox.exceptions import SandboxTimeoutError
from tracecat.sandbox.executor import NsjailExecutor
from tracecat.sandbox.types import ResourceLimits, SandboxConfig

MCP_STDIO_PROBE_WORKFLOW_ID_PREFIX = "mcp-stdio-probe"
MCP_STDIO_PROBE_ACTIVITY_NAME = "probe_stdio_mcp_connection_activity"
MCP_STDIO_PROBE_TIMEOUT_CAP = 60

_URL_PATTERN = re.compile(r"\bhttps?://\S+", re.IGNORECASE)
_URL_USERINFO_PATTERN = re.compile(r"^(https?://)[^/@]*@", re.IGNORECASE)

_SANDBOX_PROBE_SCRIPT = r"""
from __future__ import annotations

import asyncio
import json
import re
import traceback
from pathlib import Path
from typing import Any

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

URL_PATTERN = re.compile(r"\bhttps?://\S+", re.IGNORECASE)
URL_USERINFO_PATTERN = re.compile(r"^(https?://)[^/@]*@", re.IGNORECASE)


def sanitize_urls(text: str) -> str:
    def sanitize(match: re.Match[str]) -> str:
        url = URL_USERINFO_PATTERN.sub(r"\1", match.group(0))
        return re.sub(r"[?#].*$", "", url)

    return URL_PATTERN.sub(sanitize, text)


def redact(text: str, env: dict[str, str]) -> str:
    sanitized = sanitize_urls(text)
    for value in env.values():
        if isinstance(value, str) and len(value) >= 4:
            sanitized = sanitized.replace(value, "[redacted]")
    return sanitized[:2000]


def write_result(result: dict[str, Any]) -> None:
    Path("result.json").write_text(json.dumps(result), encoding="utf-8")


async def main() -> None:
    payload = json.loads(Path("input.json").read_text(encoding="utf-8"))
    command = payload["command"]
    args = payload.get("args") or []
    env = payload.get("env") or {}
    timeout = int(payload.get("timeout") or 30)
    stderr_log = Path("mcp-server.stderr.log")

    try:
        transport = StdioTransport(
            command=command,
            args=args,
            env=env,
            cwd="/tmp",
            keep_alive=False,
            log_file=stderr_log,
        )
        async with asyncio.timeout(timeout):
            async with Client(transport) as client:
                tools = await client.list_tools()
        write_result(
            {
                "success": True,
                "output": {
                    "tools": [
                        {
                            "name": tool.name,
                            "description": tool.description,
                        }
                        for tool in tools
                    ]
                },
                "stdout": "",
                "stderr": "",
                "error": None,
            }
        )
    except TimeoutError:
        write_result(
            {
                "success": False,
                "output": None,
                "stdout": "",
                "stderr": "",
                "error": f"Timed out after {timeout}s while probing stdio MCP server",
            }
        )
    except BaseException as exc:
        stderr = ""
        if stderr_log.exists():
            stderr = stderr_log.read_text(encoding="utf-8", errors="replace")
        detail = f"{type(exc).__name__}: {exc}"
        if stderr:
            detail = f"{detail}\n{stderr}"
        else:
            detail = f"{detail}\n{traceback.format_exc(limit=3)}"
        write_result(
            {
                "success": False,
                "output": None,
                "stdout": "",
                "stderr": "",
                "error": redact(detail, env),
            }
        )


if __name__ == "__main__":
    asyncio.run(main())
"""


class StdioMCPProbeInput(BaseModel):
    """Input for a saved stdio MCP probe workflow/activity."""

    mcp_integration_id: uuid.UUID
    role: Role


class StdioMCPProbeResult(BaseModel):
    """Result from sandboxed stdio MCP probing."""

    success: bool
    tools: list[MCPToolSummary] = Field(default_factory=list)
    message: str
    error: str | None = None


def build_stdio_mcp_probe_workflow_id() -> str:
    """Return a unique Temporal workflow id for a stdio MCP probe."""
    return f"{MCP_STDIO_PROBE_WORKFLOW_ID_PREFIX}/{uuid.uuid4()}"


def sanitize_stdio_probe_error(
    text: str | None, *, env: dict[str, str] | None = None
) -> str:
    """Remove obvious secret-bearing text from probe errors."""
    if not text:
        return "Stdio MCP probe failed"

    def _sanitize_url(match: re.Match[str]) -> str:
        url = _URL_USERINFO_PATTERN.sub(r"\1", match.group(0))
        return re.sub(r"[?#].*$", "", url)

    sanitized = _URL_PATTERN.sub(_sanitize_url, text)
    for value in (env or {}).values():
        if isinstance(value, str) and len(value) >= 4:
            sanitized = sanitized.replace(value, "[redacted]")
    return sanitized[:2000]


def _site_packages_dir() -> Path:
    site_packages = sysconfig.get_path("purelib")
    if not site_packages:
        raise RuntimeError("Could not resolve Python site-packages path")
    return Path(site_packages)


def _parse_probe_tools(output: object) -> list[MCPToolSummary]:
    if not isinstance(output, dict):
        return []
    raw_tools = output.get("tools")
    if not isinstance(raw_tools, list):
        return []
    tools: list[MCPToolSummary] = []
    for raw_tool in raw_tools:
        if not isinstance(raw_tool, dict):
            continue
        name = raw_tool.get("name")
        if not isinstance(name, str) or not name:
            continue
        description = raw_tool.get("description")
        tools.append(
            MCPToolSummary(
                name=name,
                description=description if isinstance(description, str) else None,
            )
        )
    return tools


async def probe_stdio_mcp_tools_in_sandbox(
    *,
    command: str,
    args: list[str] | None,
    env: dict[str, str] | None,
    timeout: int | None,
) -> StdioMCPProbeResult:
    """Run a stdio MCP server inside nsjail and list its tools."""
    timeout_seconds = min(timeout or 30, MCP_STDIO_PROBE_TIMEOUT_CAP)
    payload = {
        "command": command,
        "args": args or [],
        "env": env or {},
        "timeout": timeout_seconds,
    }

    try:
        with tempfile.TemporaryDirectory(prefix="mcp-stdio-probe-") as temp_dir:
            job_dir = Path(temp_dir)
            (job_dir / "probe.py").write_text(
                textwrap.dedent(_SANDBOX_PROBE_SCRIPT).lstrip(),
                encoding="utf-8",
            )
            (job_dir / "input.json").write_bytes(orjson.dumps(payload))

            sandbox = NsjailExecutor()
            result = await sandbox.execute(
                job_dir,
                SandboxConfig(
                    network_enabled=True,
                    resources=ResourceLimits(
                        memory_mb=1024,
                        cpu_seconds=timeout_seconds,
                        max_open_files=512,
                        max_processes=128,
                        timeout_seconds=timeout_seconds,
                    ),
                    python_path_dirs=[_site_packages_dir()],
                ),
                script_name="probe.py",
            )
    except SandboxTimeoutError:
        return StdioMCPProbeResult(
            success=False,
            message="Connection to the MCP server timed out",
            error=f"Timed out after {timeout_seconds}s while probing stdio MCP server",
        )
    except Exception as exc:
        error = sanitize_stdio_probe_error(str(exc), env=env)
        return StdioMCPProbeResult(
            success=False,
            message="Failed to run stdio MCP probe in the sandbox",
            error=error,
        )

    if not result.success:
        error = sanitize_stdio_probe_error(result.error or result.stderr, env=env)
        return StdioMCPProbeResult(
            success=False,
            message="Failed to connect to the stdio MCP server",
            error=error,
        )

    tools = _parse_probe_tools(result.output)
    return StdioMCPProbeResult(
        success=True,
        tools=tools,
        message=f"Connected successfully — {len(tools)} tools available",
    )
