"""Sandboxed stdio MCP connection probing."""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import signal
import sysconfig
import tempfile
import textwrap
import uuid
from contextlib import suppress
from pathlib import Path

import orjson
from pydantic import BaseModel, Field

from tracecat.auth.types import Role
from tracecat.integrations.schemas import MCPToolSummary
from tracecat.logger import logger
from tracecat.sandbox.exceptions import SandboxTimeoutError
from tracecat.sandbox.executor import NsjailExecutor
from tracecat.sandbox.types import ResourceLimits, SandboxConfig, SandboxResult
from tracecat.sandbox.utils import is_nsjail_available, pid_namespace_available

MCP_STDIO_PROBE_WORKFLOW_ID_PREFIX = "mcp-stdio-probe"
MCP_STDIO_PROBE_ACTIVITY_NAME = "probe_stdio_mcp_connection_activity"
MCP_STDIO_DRAFT_PROBE_ACTIVITY_NAME = "probe_stdio_mcp_draft_connection_activity"
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


class StdioMCPDraftProbeInput(BaseModel):
    """Input for a draft/unsaved stdio MCP probe workflow/activity.

    Carries the candidate config directly. ``stdio_env`` holds template
    references (e.g. ``${{ SECRETS.x }}``) and workspace-owned plaintext
    config — the same material persisted on save — which is re-resolved
    against workspace secrets inside the executor activity, so no resolved
    secret value crosses the workflow boundary.
    """

    command: str
    args: list[str] | None = None
    stdio_env: dict[str, str] | None = None
    timeout: int | None = None
    mcp_integration_id: uuid.UUID
    mcp_integration_slug: str
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


def _kill_probe_process_tree(process: asyncio.subprocess.Process) -> None:
    """Kill the probe and its spawned MCP server on timeout."""
    with suppress(ProcessLookupError):
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    with suppress(ProcessLookupError):
        process.kill()


async def _execute_probe_without_nsjail(
    job_dir: Path,
    *,
    timeout_seconds: int,
) -> SandboxResult:
    """Run the probe with best-effort PID isolation when nsjail is unavailable.

    Mirrors ``UnsafePidExecutor``: ``unshare --pid --fork --kill-child`` when
    the host supports it, otherwise a plain subprocess in its own session so
    the process tree can still be reaped on timeout. There is no filesystem,
    network, or resource isolation on this path.
    """
    python_path = shutil.which("python3") or "python3"
    cmd = [python_path, "probe.py"]
    if await pid_namespace_available():
        cmd = ["unshare", "--pid", "--fork", "--kill-child", *cmd]

    exec_env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", "/tmp"),
        "PYTHONPATH": str(_site_packages_dir()),
    }
    process = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=job_dir,
        env=exec_env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    try:
        # The +10s buffer lets the probe script's internal timeout produce a
        # graceful result before the hard kill, matching the nsjail path.
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout_seconds + 10,
        )
    except TimeoutError as exc:
        _kill_probe_process_tree(process)
        await process.wait()
        raise SandboxTimeoutError(
            f"Timed out after {timeout_seconds}s while probing stdio MCP server"
        ) from exc

    stdout = stdout_bytes.decode(errors="replace")
    stderr = stderr_bytes.decode(errors="replace")
    result_path = job_dir / "result.json"
    if result_path.exists():
        try:
            result_data = orjson.loads(result_path.read_bytes())
        except orjson.JSONDecodeError:
            result_data = None
        if isinstance(result_data, dict):
            return SandboxResult(
                success=result_data.get("success", False),
                output=result_data.get("output"),
                stdout=result_data.get("stdout", stdout),
                stderr=result_data.get("stderr", stderr),
                error=result_data.get("error"),
                exit_code=process.returncode,
            )

    return SandboxResult(
        success=False,
        error="Stdio MCP probe exited without producing a result",
        stdout=stdout,
        stderr=stderr[:500],
        exit_code=process.returncode,
    )


async def probe_stdio_mcp_tools_in_sandbox(
    *,
    command: str,
    args: list[str] | None,
    env: dict[str, str] | None,
    timeout: int | None,
) -> StdioMCPProbeResult:
    """Run a stdio MCP server in a sandbox and list its tools.

    Uses nsjail when available; otherwise falls back to the same best-effort
    PID-level isolation that unsandboxed Python actions use.
    """
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

            if is_nsjail_available():
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
            else:
                logger.warning(
                    "Running stdio MCP probe without nsjail; "
                    "applying best-effort PID isolation only"
                )
                result = await _execute_probe_without_nsjail(
                    job_dir,
                    timeout_seconds=timeout_seconds,
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
