"""Custom Claude SDK transport that runs Claude Code inside the sandbox."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path
from time import perf_counter
from typing import Any, TypedDict

import claude_agent_sdk
import orjson
from claude_agent_sdk import ClaudeAgentOptions, Transport
from claude_agent_sdk._errors import (
    CLIConnectionError,
    ProcessError,
)
from claude_agent_sdk._errors import CLIJSONDecodeError as SDKJSONDecodeError
from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport
from claude_agent_sdk._version import __version__

from tracecat.agent.runtime.claude_code.session_paths import ClaudeSandboxPathMapping
from tracecat.agent.sandbox.nsjail import (
    SpawnedRuntime,
    cleanup_spawned_runtime,
    spawn_jailed_runtime,
)
from tracecat.logger import logger


class ClaudeShimInitPayload(TypedDict):
    """Init payload consumed by the sandbox shim process."""

    command: list[str]
    env: dict[str, str]
    cwd: str


class SandboxedCLITransport(Transport):
    """Claude SDK transport that runs the Claude CLI inside the sandbox shim."""

    ENTRYPOINT_MODULE = "tracecat.agent.sandbox.shim_entrypoint"
    JAILED_SITE_PACKAGES_ROOT = Path("/site-packages")
    _MAX_BUFFER_SIZE = 1024 * 1024

    def __init__(
        self,
        *,
        options: ClaudeAgentOptions,
        session_id: str,
        socket_dir: Path,
        llm_socket_path: Path,
        job_dir: Path,
        path_mapping: ClaudeSandboxPathMapping,
        enable_internet_access: bool,
    ) -> None:
        self._options = options
        self._session_id = session_id
        self._socket_dir = socket_dir
        self._llm_socket_path = llm_socket_path
        self._job_dir = job_dir
        self._path_mapping = path_mapping
        self._enable_internet_access = enable_internet_access
        self._process: asyncio.subprocess.Process | None = None
        self._spawned_runtime: SpawnedRuntime | None = None
        self._ready = False
        self._write_lock = asyncio.Lock()
        self._stderr_task: asyncio.Task[None] | None = None
        self._stderr_buffer: list[str] = []
        self._connect_started_at: float | None = None
        self._logged_first_message = False

    def _log_benchmark_phase(self, phase: str, **extra: object) -> None:
        """Emit a temporary structured benchmark log for transport phases."""
        if self._connect_started_at is None:
            elapsed_ms = None
        else:
            elapsed_ms = round((perf_counter() - self._connect_started_at) * 1000, 2)
        logger.info(
            "Agent benchmark phase",
            phase=phase,
            elapsed_ms=elapsed_ms,
            session_id=self._session_id,
            component="transport",
            **extra,
        )

    async def connect(self) -> None:
        """Start the sandbox shim and prepare Claude stream-json I/O."""
        if self._process is not None:
            return

        self._connect_started_at = perf_counter()
        self._logged_first_message = False
        self._log_benchmark_phase("broker_transport_connect_start")
        command = await self._build_claude_command()
        init_payload: ClaudeShimInitPayload = {
            "command": command,
            "env": self._build_claude_env_overlay(),
            "cwd": str(self._path_mapping.runtime_cwd),
        }
        init_payload_path = self._job_dir / "claude-shim-init.json"
        await asyncio.to_thread(
            init_payload_path.write_bytes, orjson.dumps(init_payload)
        )
        self._log_benchmark_phase(
            "broker_transport_init_written",
            init_payload_path=str(init_payload_path),
        )

        self._spawned_runtime = await spawn_jailed_runtime(
            socket_dir=self._socket_dir,
            init_payload_path=init_payload_path,
            llm_socket_path=self._llm_socket_path,
            entrypoint_module=self.ENTRYPOINT_MODULE,
            control_socket_required=False,
            pipe_stdin=True,
            job_dir=self._job_dir,
            session_home_dir=self._path_mapping.host_home_dir,
            session_project_dir=self._path_mapping.host_project_dir,
            enable_internet_access=self._enable_internet_access,
        )
        self._process = self._spawned_runtime.process
        if self._process.stdin is None or self._process.stdout is None:
            raise CLIConnectionError("Sandbox shim stdio was not initialized")

        self._ready = True
        self._log_benchmark_phase(
            "broker_transport_sandbox_spawned",
            pid=self._process.pid,
        )
        if self._process.stderr is not None:
            self._stderr_task = asyncio.create_task(self._drain_stderr())

    async def write(self, data: str) -> None:
        """Write raw stream-json data to the sandbox shim stdin."""
        async with self._write_lock:
            if not self._ready or self._process is None or self._process.stdin is None:
                raise CLIConnectionError("Sandbox transport is not ready for writing")
            if self._process.returncode is not None:
                raise CLIConnectionError(
                    f"Sandbox shim exited with code {self._process.returncode}"
                )

            self._process.stdin.write(data.encode("utf-8"))
            await self._process.stdin.drain()

    def read_messages(self) -> AsyncIterator[dict[str, Any]]:
        """Read JSON messages emitted by sandboxed Claude Code."""
        return self._read_messages_impl()

    async def _read_messages_impl(self) -> AsyncIterator[dict[str, Any]]:
        """Parse stream-json lines emitted by the sandbox shim."""
        if self._process is None or self._process.stdout is None:
            raise CLIConnectionError("Sandbox transport is not connected")

        json_buffer = ""
        while line_bytes := await self._process.stdout.readline():
            line = line_bytes.decode("utf-8", errors="replace").strip()
            if not line:
                continue

            for json_line in line.split("\n"):
                stripped = json_line.strip()
                if not stripped:
                    continue
                if not json_buffer and not stripped.startswith("{"):
                    logger.debug("Skipping non-JSON sandbox stdout line", line=stripped)
                    continue

                json_buffer += stripped
                if len(json_buffer) > self._MAX_BUFFER_SIZE:
                    buffer_length = len(json_buffer)
                    json_buffer = ""
                    raise SDKJSONDecodeError(
                        f"JSON message exceeded maximum buffer size of {self._MAX_BUFFER_SIZE} bytes",
                        ValueError(
                            f"Buffer size {buffer_length} exceeds limit {self._MAX_BUFFER_SIZE}"
                        ),
                    )

                try:
                    data = orjson.loads(json_buffer)
                except orjson.JSONDecodeError:
                    continue

                json_buffer = ""
                if not self._logged_first_message:
                    self._logged_first_message = True
                    self._log_benchmark_phase("broker_transport_first_stdout_message")
                yield data

        if self._process.returncode is None:
            await self._process.wait()

        returncode = self._process.returncode or 0
        if returncode != 0:
            stderr_output = await self._collect_error_stderr()
            raise ProcessError(
                f"Sandbox shim failed with exit code {returncode}",
                exit_code=returncode,
                stderr=stderr_output,
            )

    async def close(self) -> None:
        """Close the sandbox shim and clean up job-scoped spawn resources."""
        async with self._write_lock:
            self._ready = False
            if self._process is not None and self._process.stdin is not None:
                self._process.stdin.close()

        if self._stderr_task is not None:
            self._stderr_task.cancel()
            try:
                await self._stderr_task
            except asyncio.CancelledError:
                pass
            self._stderr_task = None

        if self._process is not None and self._process.returncode is None:
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except TimeoutError:
                self._process.terminate()
                try:
                    await asyncio.wait_for(self._process.wait(), timeout=5.0)
                except TimeoutError:
                    self._process.kill()
                    await self._process.wait()

        self._process = None
        if self._spawned_runtime is not None:
            cleanup_spawned_runtime(self._spawned_runtime)
            self._spawned_runtime = None

    def is_ready(self) -> bool:
        """Return whether the shim is ready for Claude SDK traffic."""
        return self._ready

    async def end_input(self) -> None:
        """Close the shim stdin to signal end-of-input."""
        async with self._write_lock:
            if self._process is not None and self._process.stdin is not None:
                self._process.stdin.close()

    async def _build_claude_command(self) -> list[str]:
        """Reuse the SDK's CLI command builder for option parity."""
        helper = SubprocessCLITransport(prompt="", options=self._options)
        if helper._cli_path is None:
            helper._cli_path = await asyncio.to_thread(helper._find_cli)
        if not os.environ.get("CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK"):
            await helper._check_claude_version()
        return self._rewrite_command_for_jail(helper._build_command())

    @classmethod
    def _rewrite_command_for_jail(cls, command: list[str]) -> list[str]:
        """Translate SDK-built command paths from host to jailed filesystem layout."""
        if not command:
            raise CLIConnectionError("Claude command is empty")

        executable = Path(command[0]).resolve()
        host_site_packages_root = (
            Path(claude_agent_sdk.__file__).resolve().parent.parent
        )
        try:
            relative_executable = executable.relative_to(host_site_packages_root)
        except ValueError:
            return command

        return [
            str(cls.JAILED_SITE_PACKAGES_ROOT / relative_executable),
            *command[1:],
        ]

    def _build_claude_env_overlay(self) -> dict[str, str]:
        """Build the Claude child env overlay applied inside the shim."""
        env = {
            "CLAUDE_CODE_ENTRYPOINT": "sdk-py",
            "CLAUDE_AGENT_SDK_VERSION": __version__,
            "HOME": str(self._path_mapping.runtime_home_dir),
            "PWD": str(self._path_mapping.runtime_cwd),
            **self._options.env,
        }
        if self._options.enable_file_checkpointing:
            env["CLAUDE_CODE_ENABLE_SDK_FILE_CHECKPOINTING"] = "true"
        if skip_version_check := os.environ.get("CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK"):
            env["CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK"] = skip_version_check
        return env

    async def _drain_stderr(self) -> None:
        """Forward shim stderr lines into the configured Claude stderr callback."""
        if self._process is None or self._process.stderr is None:
            return

        while line_bytes := await self._process.stderr.readline():
            line = line_bytes.decode("utf-8", errors="replace").rstrip()
            if not line:
                continue
            self._stderr_buffer.append(line)
            if len(self._stderr_buffer) > 200:
                del self._stderr_buffer[:-200]
            if self._options.stderr is not None:
                self._options.stderr(line)
            else:
                logger.warning("Sandbox shim stderr", line=line)

    async def _collect_error_stderr(self) -> str:
        """Return the buffered stderr tail for a failed shim process."""
        if self._stderr_task is not None:
            try:
                await asyncio.wait_for(self._stderr_task, timeout=1.0)
            except TimeoutError:
                logger.warning(
                    "Timed out waiting for sandbox stderr drain",
                    session_id=self._session_id,
                )
            finally:
                if self._stderr_task.done():
                    self._stderr_task = None

        if self._process is not None and self._process.stderr is not None:
            remaining = await self._process.stderr.read()
            if remaining:
                for line in remaining.decode("utf-8", errors="replace").splitlines():
                    if line:
                        self._stderr_buffer.append(line)

        if not self._stderr_buffer:
            return "No stderr captured"

        stderr_output = "\n".join(self._stderr_buffer[-200:])
        if len(stderr_output) > 4000:
            return stderr_output[-4000:]
        return stderr_output
