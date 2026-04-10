"""Temporal activities for agent runtime execution."""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import AliasChoices, BaseModel, Field
from temporalio import activity

from tracecat.agent.common.config import (
    TRACECAT__AGENT_SANDBOX_MEMORY_MB,
    TRACECAT__AGENT_SANDBOX_TIMEOUT,
    TRACECAT__DISABLE_NSJAIL,
)
from tracecat.agent.common.exceptions import AgentSandboxExecutionError
from tracecat.agent.common.stream_types import ToolCallContent
from tracecat.agent.common.types import MCPToolDefinition
from tracecat.agent.executor.loopback import (
    LoopbackHandler,
    LoopbackInput,
    LoopbackResult,
)
from tracecat.agent.llm_proxy.core import TracecatLLMProxy
from tracecat.agent.sandbox.llm_proxy import LLM_SOCKET_NAME, LLMSocketProxy
from tracecat.agent.sandbox.nsjail import spawn_jailed_runtime
from tracecat.agent.session.service import AgentSessionService
from tracecat.agent.skill.service import SkillService
from tracecat.agent.types import AgentConfig
from tracecat.auth.types import Role
from tracecat.chat.schemas import ChatMessage
from tracecat.config import (
    TRACECAT__AGENT_SKILL_CACHE_DIR,
    TRACECAT__AGENT_SKILL_CACHE_MAX_CONCURRENT_DOWNLOADS,
)
from tracecat.logger import logger
from tracecat.registry.lock.types import RegistryLock
from tracecat.storage import blob

from .schemas import ApprovedToolCall, DeniedToolCall, ToolExecutionResult


class AgentExecutorInput(BaseModel):
    """Input for the agent executor activity.

    On resume after approval, the sdk_session_data contains the proper tool_result
    entry (inserted by the approval reconciliation activity before reload), so the
    runtime just resumes normally.
    """

    model_config = {"arbitrary_types_allowed": True}

    session_id: uuid.UUID
    workspace_id: uuid.UUID
    user_prompt: str
    config: AgentConfig
    # Role for context
    role: Role
    # Authentication tokens (minted by workflow before calling activity)
    mcp_auth_token: str
    llm_gateway_auth_token: str = Field(
        validation_alias=AliasChoices("llm_gateway_auth_token", "litellm_auth_token"),
    )
    # Resolved tool definitions
    allowed_actions: dict[str, MCPToolDefinition] | None = None
    # Session resume data (from previous run, includes tool_result for approval flow)
    sdk_session_id: str | None = None
    sdk_session_data: str | None = None
    # True when resuming after approval decision (continuation prompt should be internal)
    is_approval_continuation: bool = False
    # True when forking from parent session (SDK should use fork_session=True)
    is_fork: bool = False


class AgentExecutorResult(BaseModel):
    """Result from the agent executor activity."""

    success: bool
    error: str | None = None
    approval_requested: bool = False
    approval_items: list[ToolCallContent] | None = None
    messages: list[ChatMessage] | None = None
    output: Any = Field(
        default=None,
        validation_alias=AliasChoices("output", "structured_output", "result_output"),
        serialization_alias="output",
    )
    result_usage: dict[str, Any] | None = None
    result_num_turns: int | None = None


class ExecuteApprovedToolsInput(BaseModel):
    """Deprecated compatibility input for approval-path tool execution."""

    model_config = {"arbitrary_types_allowed": True}

    session_id: uuid.UUID
    workspace_id: uuid.UUID
    role: Role
    approved_tools: list[ApprovedToolCall]
    denied_tools: list[DeniedToolCall]
    allowed_actions: list[str]
    registry_lock: RegistryLock


class ExecuteApprovedToolsResult(BaseModel):
    """Deprecated compatibility result for approval-path tool execution."""

    results: list[ToolExecutionResult]
    success: bool = True
    error: str | None = None


@dataclass
class SandboxedAgentExecutor:
    """Executes agent in NSJail sandbox.

    This executor:
    1. Creates a job directory with Unix sockets
    2. Spawns the NSJail process
    3. Uses LoopbackHandler to communicate with runtime
    4. Cleans up on completion
    """

    input: AgentExecutorInput
    timeout_seconds: int = field(
        default_factory=lambda: TRACECAT__AGENT_SANDBOX_TIMEOUT
    )
    memory_mb: int = field(default_factory=lambda: TRACECAT__AGENT_SANDBOX_MEMORY_MB)

    # Internal state
    _job_dir: Path | None = field(default=None, init=False, repr=False)
    _process: asyncio.subprocess.Process | None = field(
        default=None, init=False, repr=False
    )
    _loopback_result: asyncio.Future[LoopbackResult] | None = field(
        default=None, init=False, repr=False
    )
    _llm_proxy: LLMSocketProxy | None = field(default=None, init=False, repr=False)
    _fatal_error: str | None = field(default=None, init=False, repr=False)
    _fatal_error_event: asyncio.Event = field(
        default_factory=asyncio.Event, init=False, repr=False
    )

    def _create_llm_socket_proxy(self, socket_path: Path) -> LLMSocketProxy:
        """Create the host-side LLM socket proxy for this execution."""

        def on_error(error_msg: str) -> None:
            self._fatal_error = error_msg
            self._fatal_error_event.set()

        return LLMSocketProxy(
            socket_path=socket_path,
            tracecat_proxy=TracecatLLMProxy.build(),
            on_error=on_error,
        )

    async def run(self) -> AgentExecutorResult:
        """Execute the agent in an NSJail sandbox.

        Returns:
            AgentExecutorResult with success status and any session updates.
        """
        result = AgentExecutorResult(success=False)

        try:
            # Create job directory with sockets
            self._job_dir = await self._create_job_directory()
            socket_dir = self._job_dir / "sockets"

            # Create loopback handler
            loopback_input = LoopbackInput(
                session_id=self.input.session_id,
                workspace_id=self.input.workspace_id,
                user_prompt=self.input.user_prompt,
                config=self.input.config,
                mcp_auth_token=self.input.mcp_auth_token,
                llm_gateway_auth_token=self.input.llm_gateway_auth_token,
                socket_dir=socket_dir,
                allowed_actions=self.input.allowed_actions,
                sdk_session_id=self.input.sdk_session_id,
                sdk_session_data=self.input.sdk_session_data,
                is_approval_continuation=self.input.is_approval_continuation,
                is_fork=self.input.is_fork,
            )
            handler = LoopbackHandler(input=loopback_input)

            async def emit_stream_error(error_msg: str) -> None:
                """Emit errors through the same sink abstraction as loopback."""
                try:
                    await handler.emit_terminal_error(error_msg)
                except Exception:
                    logger.warning(
                        "Failed to emit terminal stream error",
                        session_id=self.input.session_id,
                    )

            # Future to capture loopback result
            self._loopback_result = asyncio.get_running_loop().create_future()

            async def connection_callback(
                reader: asyncio.StreamReader,
                writer: asyncio.StreamWriter,
            ) -> None:
                """Callback for Unix socket server."""
                logger.debug("Connection callback started")
                try:
                    loopback_result = await handler.handle_connection(reader, writer)
                    logger.info(
                        "Loopback handler completed",
                        success=loopback_result.success,
                        error=loopback_result.error,
                        approval_requested=loopback_result.approval_requested,
                    )
                    if self._loopback_result and not self._loopback_result.done():
                        self._loopback_result.set_result(loopback_result)
                        logger.debug("Future result set")
                    else:
                        logger.warning(
                            "Future already done or None",
                            future_done=self._loopback_result.done()
                            if self._loopback_result
                            else None,
                        )
                except Exception as e:
                    logger.exception("Connection callback error", error=str(e))
                    if self._loopback_result and not self._loopback_result.done():
                        self._loopback_result.set_exception(e)

            # Start control socket server (hardcoded socket name)
            control_socket_path = socket_dir / "control.sock"
            logger.info(
                "Starting control socket server",
                socket_path=str(control_socket_path),
            )

            # Start the host-side LLM socket proxy for this execution.
            llm_socket_path = socket_dir / LLM_SOCKET_NAME
            self._llm_proxy = self._create_llm_socket_proxy(llm_socket_path)
            await self._llm_proxy.start()
            logger.info(
                "Started LLM socket proxy",
                socket_path=str(llm_socket_path),
            )

            # Set umask before socket creation to ensure 0o600 permissions from the start
            old_umask = os.umask(0o177)
            try:
                server = await asyncio.start_unix_server(
                    connection_callback,
                    path=str(control_socket_path),
                )
            finally:
                os.umask(old_umask)

            async with server:
                runtime_result = await spawn_jailed_runtime(
                    socket_dir=socket_dir,
                    llm_socket_path=llm_socket_path,
                    enable_internet_access=self.input.config.enable_internet_access,
                    home_dir=self._job_dir / "home",
                    skills_dir=self._job_dir / "home" / ".claude" / "skills",
                )
                self._process = runtime_result.process
                logger.info(
                    "Agent runtime process spawned",
                    pid=self._process.pid,
                    session_id=self.input.session_id,
                    mode="direct" if TRACECAT__DISABLE_NSJAIL else "nsjail",
                )

                # Wait for loopback to complete OR fatal error from LLM proxy OR process exit
                # We poll with short timeout to allow heartbeats to Temporal
                logger.debug("Waiting for loopback result or fatal error")
                heartbeat_interval = 30  # seconds
                elapsed = 0

                # Create a task to wait for fatal error event
                async def wait_fatal_error() -> str:
                    await self._fatal_error_event.wait()
                    return self._fatal_error or "Unknown LLM error"

                fatal_error_task = asyncio.create_task(wait_fatal_error())

                # Create a task to monitor process exit (to capture crash errors)
                async def wait_process_exit() -> tuple[int, str]:
                    """Wait for process to exit and capture stderr."""
                    if self._process is None:
                        return -1, "Process not started"
                    _, stderr_bytes = await self._process.communicate()
                    stderr = (
                        stderr_bytes.decode("utf-8", errors="replace")
                        if stderr_bytes
                        else ""
                    )
                    return self._process.returncode or 0, stderr

                process_exit_task = asyncio.create_task(wait_process_exit())

                try:

                    def _apply_loopback_result(loopback_result: LoopbackResult) -> None:
                        """Copy loopback result fields into the activity result."""
                        result.success = loopback_result.success
                        result.error = loopback_result.error
                        result.approval_requested = loopback_result.approval_requested
                        result.approval_items = loopback_result.approval_items or None
                        result.output = loopback_result.output
                        result.result_usage = loopback_result.result_usage
                        result.result_num_turns = loopback_result.result_num_turns

                    while elapsed < self.timeout_seconds:
                        # Wait for either loopback result, fatal error, or process exit
                        done, _ = await asyncio.wait(
                            [
                                asyncio.ensure_future(
                                    asyncio.shield(self._loopback_result)
                                ),
                                fatal_error_task,
                                process_exit_task,
                            ],
                            timeout=heartbeat_interval,
                            return_when=asyncio.FIRST_COMPLETED,
                        )

                        if not done:
                            # Timeout - send heartbeat and continue waiting
                            elapsed += heartbeat_interval
                            activity.heartbeat(
                                f"Agent running: {self.input.session_id} ({elapsed}s elapsed)"
                            )
                            logger.debug(
                                "Heartbeat sent, continuing to wait",
                                elapsed=elapsed,
                                timeout=self.timeout_seconds,
                            )
                            continue

                        # Check if fatal error task completed
                        if fatal_error_task in done:
                            # Fatal error from LLM proxy - fail immediately
                            error_msg = fatal_error_task.result()
                            logger.error(
                                "Fatal LLM error detected, terminating agent",
                                error=error_msg,
                            )
                            result.error = error_msg

                            # Kill the process
                            if self._process and self._process.returncode is None:
                                self._process.kill()

                            await emit_stream_error(error_msg)
                            break

                        # Check if process exited before loopback completion
                        if (
                            process_exit_task in done
                            and not self._loopback_result.done()
                        ):
                            returncode, stderr = process_exit_task.result()
                            if returncode == 0:
                                # Graceful process exit can race with loopback future resolution.
                                # Give loopback a short window to finish before treating as an error.
                                logger.warning(
                                    "Runtime exited before loopback future was ready; waiting briefly",
                                    returncode=returncode,
                                    session_id=self.input.session_id,
                                )
                                try:
                                    loopback_result = await asyncio.wait_for(
                                        asyncio.shield(self._loopback_result),
                                        timeout=5.0,
                                    )
                                except TimeoutError:
                                    error_msg = (
                                        "Runtime exited cleanly but loopback result "
                                        "was not received"
                                    )
                                    logger.error(
                                        "Missing loopback result after clean runtime exit",
                                        returncode=returncode,
                                        session_id=self.input.session_id,
                                    )
                                    result.error = error_msg
                                    await emit_stream_error(error_msg)
                                    break

                                logger.info(
                                    "Loopback result received after clean runtime exit",
                                    success=loopback_result.success,
                                    error=loopback_result.error,
                                )
                                _apply_loopback_result(loopback_result)
                                break

                            # Non-zero exit code before loopback completion: treat as crash
                            # Log stderr - show last 4000 chars which typically contain the actual error
                            # (NSJail mount logs consume the beginning)
                            if stderr:
                                # Log the tail of stderr (where Python errors typically appear)
                                stderr_tail = (
                                    stderr[-4000:] if len(stderr) > 4000 else stderr
                                )
                                logger.error(
                                    "Runtime process stderr (tail)",
                                    stderr=stderr_tail,
                                )
                            error_msg = (
                                f"Runtime process exited with code {returncode} "
                                f"before connecting. stderr: {stderr[-1000:] if stderr else 'empty'}"
                            )
                            logger.error(
                                "Runtime process crashed",
                                returncode=returncode,
                                stderr_tail=stderr[-500:] if stderr else None,
                            )
                            result.error = error_msg

                            await emit_stream_error(error_msg)
                            break

                        # Loopback completed
                        loopback_result = self._loopback_result.result()
                        logger.info(
                            "Loopback result received",
                            success=loopback_result.success,
                            error=loopback_result.error,
                        )
                        _apply_loopback_result(loopback_result)
                        break
                    else:
                        # Exceeded total timeout
                        logger.error("Agent execution timed out waiting for loopback")
                        result.error = (
                            f"Agent execution timed out after {self.timeout_seconds}s"
                        )
                        await emit_stream_error(result.error)

                except asyncio.CancelledError:
                    logger.error(
                        "Loopback future was cancelled",
                        future_done=self._loopback_result.done()
                        if self._loopback_result
                        else None,
                        future_cancelled=self._loopback_result.cancelled()
                        if self._loopback_result
                        else None,
                    )
                    raise
                finally:
                    # Clean up tasks if still pending
                    for task in [fatal_error_task, process_exit_task]:
                        if not task.done():
                            task.cancel()
                            try:
                                await task
                            except asyncio.CancelledError:
                                pass

        except AgentSandboxExecutionError as e:
            logger.error("Agent sandbox execution failed", error=str(e))
            result.error = str(e)
        except Exception as e:
            logger.exception("Unexpected error in agent executor", error=str(e))
            result.error = f"Unexpected error: {e}"
        finally:
            await self._cleanup()

        return result

    async def _create_job_directory(self) -> Path:
        """Create a temporary job directory with socket subdirectory."""
        job_id = str(self.input.session_id)[:12]
        # Hardcoded job socket directory for per-job control sockets
        base_dir = Path("/tmp/tracecat-agent-jobs")
        base_dir.mkdir(parents=True, exist_ok=True)

        job_dir = Path(tempfile.mkdtemp(prefix=f"agent-job-{job_id}-", dir=base_dir))
        socket_dir = job_dir / "sockets"
        socket_dir.mkdir(mode=0o700)
        home_dir = job_dir / "home"
        (home_dir / ".claude" / "skills").mkdir(parents=True, exist_ok=True)
        await self._stage_resolved_skills(home_dir / ".claude" / "skills")

        # Note: The MCP socket directory is mounted directly into NSJail at /mcp-sockets
        # so we don't need to symlink it here
        logger.debug(
            "Created job directory",
            job_dir=str(job_dir),
            socket_dir=str(socket_dir),
        )
        return job_dir

    async def _stage_resolved_skills(self, skills_dir: Path) -> None:
        """Stage resolved published skills into the per-run home directory."""

        resolved_skills = self.input.config.resolved_skills or []
        if not resolved_skills:
            return

        async with SkillService.with_session(role=self.input.role) as service:
            for resolved_skill in resolved_skills:
                cached_dir = await self._ensure_cached_skill_dir(
                    service=service,
                    manifest_sha256=resolved_skill.manifest_sha256,
                    skill_version_id=resolved_skill.skill_version_id,
                )
                await asyncio.to_thread(
                    shutil.copytree,
                    cached_dir,
                    skills_dir / resolved_skill.skill_slug,
                    dirs_exist_ok=True,
                )

    async def _ensure_cached_skill_dir(
        self,
        *,
        service: SkillService,
        manifest_sha256: str,
        skill_version_id: uuid.UUID,
    ) -> Path:
        """Populate the worker-local extracted skill cache if needed."""

        cache_root = Path(TRACECAT__AGENT_SKILL_CACHE_DIR)
        cache_root.mkdir(parents=True, exist_ok=True)
        cache_dir = cache_root / manifest_sha256
        if cache_dir.exists():
            return cache_dir

        temp_dir = cache_root / f".tmp-{manifest_sha256}-{uuid.uuid4().hex}"
        temp_dir.mkdir(parents=True, exist_ok=True)
        try:
            version_files = await service.get_version_file_materialization(
                skill_version_id
            )
            if not version_files:
                try:
                    temp_dir.rename(cache_dir)
                except FileExistsError:
                    shutil.rmtree(temp_dir, ignore_errors=True)
                return cache_dir
            semaphore = asyncio.Semaphore(
                min(
                    len(version_files),
                    TRACECAT__AGENT_SKILL_CACHE_MAX_CONCURRENT_DOWNLOADS,
                )
            )

            async def download_version_file(path: str, blob_row: Any) -> None:
                async with semaphore:
                    await blob.download_file_to_path(
                        key=blob_row.key,
                        bucket=blob_row.bucket,
                        output_path=temp_dir / path,
                        expected_sha256=blob_row.sha256,
                        max_bytes=blob_row.size_bytes,
                    )

            async with asyncio.TaskGroup() as task_group:
                for path, blob_row in version_files:
                    task_group.create_task(download_version_file(path, blob_row))
            try:
                temp_dir.rename(cache_dir)
            except FileExistsError:
                shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise
        return cache_dir

    async def _cleanup(self) -> None:
        """Clean up resources after execution."""
        # Terminate process if still running
        if self._process and self._process.returncode is None:
            logger.warning("Terminating agent runtime process")
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except TimeoutError:
                self._process.kill()
                await self._process.wait()

        # Stop LLM socket proxy
        if self._llm_proxy:
            try:
                await self._llm_proxy.stop()
            except Exception as e:
                logger.warning("Failed to stop LLM proxy", error=str(e))
            self._llm_proxy = None

        # Clean up job directory
        if self._job_dir and self._job_dir.exists():
            try:
                shutil.rmtree(self._job_dir)
                logger.debug("Cleaned up job directory", job_dir=str(self._job_dir))
            except Exception as e:
                logger.warning(
                    "Failed to clean up job directory",
                    job_dir=str(self._job_dir),
                    error=str(e),
                )


@activity.defn
async def run_agent_activity(
    input: AgentExecutorInput,
) -> AgentExecutorResult:
    """Temporal activity that runs the agent in a sandbox (or direct subprocess for testing).

    This activity:
    1. Creates a SandboxedAgentExecutor
    2. Runs the agent (in nsjail sandbox or direct subprocess depending on config)
    3. Returns the result with session updates

    When TRACECAT__DISABLE_NSJAIL=true, the agent is run as a direct subprocess
    without nsjail isolation. This is useful for testing on platforms without
    nsjail (macOS, Windows, CI environments).

    The activity is designed to be retryable - if it fails due to transient
    errors, Temporal will retry it. Session state is persisted on success
    so resumption works correctly.

    Args:
        input: Agent executor configuration and tokens.

    Returns:
        AgentExecutorResult with execution status and session data.
    """
    sandbox_mode = "direct" if TRACECAT__DISABLE_NSJAIL else "nsjail"
    activity.heartbeat(
        f"Starting agent execution ({sandbox_mode} mode): {input.session_id}"
    )

    executor = SandboxedAgentExecutor(input=input)
    result = await executor.run()

    if result.success:
        activity.heartbeat(f"Agent execution completed: {input.session_id}")
        # Fetch messages from database to include in result
        try:
            async with AgentSessionService.with_session(role=input.role) as svc:
                result.messages = await svc.list_messages(input.session_id)
        except Exception as e:
            logger.warning(
                "Failed to fetch session messages",
                session_id=str(input.session_id),
                error=str(e),
            )
    else:
        activity.heartbeat(f"Agent execution failed: {result.error}")

    return result
