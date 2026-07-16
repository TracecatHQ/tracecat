"""Sandboxed stdio MCP connection probing."""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
import sysconfig
import tempfile
import textwrap
from contextlib import suppress
from pathlib import Path

import orjson

from tracecat.agent.mcp.stdio_probe_types import (
    MCP_STDIO_PERSIST_ACTIVITY_NAME,
    MCP_STDIO_PROBE_ACTIVITY_NAME,
    MCP_STDIO_PROBE_DEFAULT_TIMEOUT,
    MCP_STDIO_PROBE_HARD_TIMEOUT_BUFFER,
    MCP_STDIO_PROBE_TIMEOUT_CAP,
    MCP_STDIO_PROBE_WORKFLOW_ID_PREFIX,
    StdioMCPPersistInput,
    StdioMCPProbeInput,
    StdioMCPProbeResult,
    StdioMCPProbeWorkflowInput,
    build_stdio_mcp_probe_workflow_id,
    sanitize_stdio_probe_error,
)
from tracecat.agent.mcp.utils import STDIO_MCP_TOOL_NAME_RE
from tracecat.integrations.schemas import MCPToolSummary
from tracecat.logger import logger
from tracecat.sandbox.exceptions import SandboxTimeoutError
from tracecat.sandbox.executor import NsjailExecutor
from tracecat.sandbox.types import ResourceLimits, SandboxConfig, SandboxResult
from tracecat.sandbox.utils import is_nsjail_available, pid_namespace_available

__all__ = [
    "MCP_STDIO_PERSIST_ACTIVITY_NAME",
    "MCP_STDIO_PROBE_ACTIVITY_NAME",
    "MCP_STDIO_PROBE_DEFAULT_TIMEOUT",
    "MCP_STDIO_PROBE_HARD_TIMEOUT_BUFFER",
    "MCP_STDIO_PROBE_TIMEOUT_CAP",
    "MCP_STDIO_PROBE_WORKFLOW_ID_PREFIX",
    "StdioMCPPersistInput",
    "StdioMCPProbeInput",
    "StdioMCPProbeResult",
    "StdioMCPProbeWorkflowInput",
    "build_stdio_mcp_probe_workflow_id",
    "probe_stdio_mcp_tools_in_sandbox",
    "sanitize_stdio_probe_error",
]

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
EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
BEARER_TOKEN_PATTERN = re.compile(
    r"\b(Bearer\s+)[A-Za-z0-9._~+/=-]+", re.IGNORECASE
)
AUTHORIZATION_VALUE_PATTERN = re.compile(
    r"\b(Authorization\s*:\s*)[^\r\n]+", re.IGNORECASE | re.MULTILINE
)
COOKIE_VALUE_PATTERN = re.compile(
    r"\b((?:Set-)?Cookie\s*:\s*)[^\r\n]+", re.IGNORECASE | re.MULTILINE
)
JWT_PATTERN = re.compile(
    r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"
)
SENSITIVE_VALUE_PATTERN = re.compile(
    r"(?P<prefix>['\"]?\b(?:api[ _-]?key|access[ _-]?token|refresh[ _-]?token|"
    r"id[ _-]?token|client[ _-]?secret|private[ _-]?key|secret[ _-]?key|token|"
    r"password|passwd|secret)\b['\"]?\s*[:=]\s*)"
    r"(?:"
    r'(?P<double_quoted>"(?:\\.|[^"\\\r\n])*")|'
    r"(?P<single_quoted>'(?:\\.|[^'\\\r\n])*')|"
    r"(?P<unquoted>[^\s,;}\]\"']+)"
    r")",
    re.IGNORECASE,
)


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
            sanitized_value = sanitize_urls(value)
            if sanitized_value != value:
                sanitized = sanitized.replace(sanitized_value, "[redacted]")
    sanitized = EMAIL_PATTERN.sub("[redacted email]", sanitized)
    sanitized = BEARER_TOKEN_PATTERN.sub(r"\1[redacted]", sanitized)
    sanitized = AUTHORIZATION_VALUE_PATTERN.sub(r"\1[redacted]", sanitized)
    sanitized = COOKIE_VALUE_PATTERN.sub(r"\1[redacted]", sanitized)
    sanitized = JWT_PATTERN.sub("[redacted token]", sanitized)
    sanitized = SENSITIVE_VALUE_PATTERN.sub(redact_sensitive_value, sanitized)
    # Dependency installers and server banners can put the useful failure at
    # the end of stderr. Keep a bounded head and tail so the outer process can
    # discard startup noise without losing the root cause.
    if len(sanitized) <= 12000:
        return sanitized
    return f"{sanitized[:1000]}\n…\n{sanitized[-10997:]}"


def redact_sensitive_value(match: re.Match[str]) -> str:
    if match.group("double_quoted") is not None:
        redacted = '"[redacted]"'
    elif match.group("single_quoted") is not None:
        redacted = "'[redacted]'"
    else:
        redacted = "[redacted]"
    return f"{match.group('prefix')}{redacted}"


def write_result(result: dict[str, Any]) -> None:
    Path("result.json").write_text(json.dumps(result), encoding="utf-8")


async def main() -> None:
    payload = json.loads(Path("input.json").read_text(encoding="utf-8"))
    command = payload["command"]
    args = payload.get("args") or []
    env = payload.get("env") or {}
    timeout = int(payload["timeout"])
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
                "output": {"error_code": "timeout"},
                "stdout": "",
                "stderr": "",
                "error": None,
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
        if not STDIO_MCP_TOOL_NAME_RE.fullmatch(name):
            logger.warning(
                "Skipping stdio MCP tool with unsupported name",
                tool_name=name,
            )
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


def _timeout_probe_result(timeout_seconds: int) -> StdioMCPProbeResult:
    """Return an actionable user-facing result for a probe timeout."""
    timeout_label = f"{timeout_seconds} second"
    if timeout_seconds != 1:
        timeout_label = f"{timeout_label}s"
    return StdioMCPProbeResult(
        success=False,
        message="MCP server verification timed out",
        error=(
            "The MCP server did not finish starting or respond to the tools request "
            f"within {timeout_label}. Check the server command, dependencies, "
            "network access, and credentials, then try again."
        ),
    )


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
    timeout_seconds = min(
        timeout or MCP_STDIO_PROBE_DEFAULT_TIMEOUT,
        MCP_STDIO_PROBE_TIMEOUT_CAP,
    )
    hard_timeout_seconds = timeout_seconds + MCP_STDIO_PROBE_HARD_TIMEOUT_BUFFER
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
                            cpu_seconds=hard_timeout_seconds,
                            max_open_files=512,
                            max_processes=128,
                            timeout_seconds=hard_timeout_seconds,
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
        return _timeout_probe_result(timeout_seconds)
    except Exception as exc:
        error = sanitize_stdio_probe_error(str(exc), env=env)
        return StdioMCPProbeResult(
            success=False,
            message="Failed to run stdio MCP probe in the sandbox",
            error=error,
        )

    if not result.success:
        match result.output:
            case {"error_code": "timeout"}:
                return _timeout_probe_result(timeout_seconds)
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
