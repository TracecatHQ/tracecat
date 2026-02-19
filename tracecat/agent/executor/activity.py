"""Temporal activities for agent execution.

This module provides:
- AgentExecutorInput/Result: Data structures for the main executor activity
- run_agent_activity: Temporal activity that spawns NSJail and runs agent
- ExecuteApprovedToolsInput/Result: Data structures for approved tool execution
- execute_approved_tools_activity: Temporal activity that executes approved tools

The main executor activity:
1. Creates job directory with sockets
2. Starts Unix socket server
3. Spawns NSJail runtime process
4. Uses LoopbackHandler to forward events to Redis
5. Cleans up resources on completion

The approved tools activity:
1. Mints a fresh JWT token (handles delayed approvals)
2. Executes each approved tool via the MCP executor
3. Returns results and denial messages for continuation
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from temporalio import activity

from tracecat.agent.common.config import (
    TRACECAT__AGENT_SANDBOX_MEMORY_MB,
    TRACECAT__AGENT_SANDBOX_TIMEOUT,
    TRACECAT__DISABLE_NSJAIL,
)
from tracecat.agent.common.exceptions import AgentSandboxExecutionError
from tracecat.agent.common.stream_types import ToolCallContent, UnifiedStreamEvent
from tracecat.agent.common.types import MCPToolDefinition
from tracecat.agent.executor.loopback import (
    LoopbackHandler,
    LoopbackInput,
)
from tracecat.agent.mcp.executor import ActionExecutionError, execute_action
from tracecat.agent.mcp.utils import normalize_mcp_tool_name
from tracecat.agent.sandbox.llm_proxy import LLM_SOCKET_NAME, LLMSocketProxy
from tracecat.agent.sandbox.nsjail import spawn_jailed_runtime
from tracecat.agent.session.service import AgentSessionService
from tracecat.agent.stream.connector import AgentStream
from tracecat.agent.tokens import MCPTokenClaims
from tracecat.agent.types import AgentConfig
from tracecat.auth.types import Role
from tracecat.chat.schemas import ChatMessage
from tracecat.executor import registry_resolver
from tracecat.logger import logger
from tracecat.registry.lock.types import RegistryLock


class AgentExecutorInput(BaseModel):
    """Input for the agent executor activity.

    On resume after approval, the sdk_session_data contains the proper tool_result
    entry (inserted by execute_approved_tools_activity before reload), so the
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
    litellm_auth_token: str
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
    output: Any = None
    result_usage: dict[str, Any] | None = None
    result_num_turns: int | None = None


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
    _loopback_result: asyncio.Future | None = field(
        default=None, init=False, repr=False
    )
    _llm_proxy: LLMSocketProxy | None = field(default=None, init=False, repr=False)
    _fatal_error: str | None = field(default=None, init=False, repr=False)
    _fatal_error_event: asyncio.Event = field(
        default_factory=asyncio.Event, init=False, repr=False
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
                litellm_auth_token=self.input.litellm_auth_token,
                socket_dir=socket_dir,
                allowed_actions=self.input.allowed_actions,
                sdk_session_id=self.input.sdk_session_id,
                sdk_session_data=self.input.sdk_session_data,
                is_approval_continuation=self.input.is_approval_continuation,
                is_fork=self.input.is_fork,
            )
            handler = LoopbackHandler(input=loopback_input)

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

            # Start LLM socket proxy (proxies HTTP to LiteLLM via Unix socket)
            llm_socket_path = socket_dir / LLM_SOCKET_NAME

            def on_llm_error(error_msg: str) -> None:
                """Callback invoked when LLM proxy detects an error."""
                self._fatal_error = error_msg
                self._fatal_error_event.set()

            self._llm_proxy = LLMSocketProxy(
                socket_path=llm_socket_path,
                on_error=on_llm_error,
            )
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

                            # Send error to stream
                            try:
                                stream = await AgentStream.new(
                                    session_id=self.input.session_id,
                                    workspace_id=self.input.workspace_id,
                                )
                                await stream.error(error_msg)
                            except Exception:
                                pass
                            break

                        # Check if process exited before connecting (crash)
                        if (
                            process_exit_task in done
                            and not self._loopback_result.done()
                        ):
                            # Process exited without connecting to loopback
                            returncode, stderr = process_exit_task.result()
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

                            # Send error to stream
                            try:
                                stream = await AgentStream.new(
                                    session_id=self.input.session_id,
                                    workspace_id=self.input.workspace_id,
                                )
                                await stream.error(error_msg)
                            except Exception:
                                pass
                            break

                        # Loopback completed
                        loopback_result = self._loopback_result.result()
                        logger.info(
                            "Loopback result received",
                            success=loopback_result.success,
                            error=loopback_result.error,
                        )
                        result.success = loopback_result.success
                        result.error = loopback_result.error
                        result.approval_requested = loopback_result.approval_requested
                        result.approval_items = loopback_result.approval_items or None
                        result.output = loopback_result.output
                        result.result_usage = loopback_result.result_usage
                        result.result_num_turns = loopback_result.result_num_turns
                        break
                    else:
                        # Exceeded total timeout
                        logger.error("Agent execution timed out waiting for loopback")
                        result.error = (
                            f"Agent execution timed out after {self.timeout_seconds}s"
                        )
                        try:
                            stream = await AgentStream.new(
                                session_id=self.input.session_id,
                                workspace_id=self.input.workspace_id,
                            )
                            await stream.error(result.error)
                        except Exception:
                            pass

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

        # Note: The MCP socket directory is mounted directly into NSJail at /mcp-sockets
        # so we don't need to symlink it here
        logger.debug(
            "Created job directory",
            job_dir=str(job_dir),
            socket_dir=str(socket_dir),
        )
        return job_dir

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
                socket_dir = self._job_dir / "sockets"
                if socket_dir.exists():
                    for f in socket_dir.iterdir():
                        # Remove files, symlinks, and sockets (is_socket() for Unix sockets)
                        if f.is_symlink() or f.is_file() or f.is_socket():
                            f.unlink()
                    socket_dir.rmdir()

                for f in self._job_dir.iterdir():
                    if f.is_file() or f.is_socket():
                        f.unlink()
                self._job_dir.rmdir()

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


# --- Approved Tools Execution Activity ---


class ApprovedToolCall(BaseModel):
    """A single approved tool call to execute."""

    tool_call_id: str
    tool_name: str
    args: dict[str, Any]


class DeniedToolCall(BaseModel):
    """A single denied tool call."""

    tool_call_id: str
    tool_name: str
    reason: str


class ToolExecutionResult(BaseModel):
    """Result from executing a single tool."""

    tool_call_id: str
    tool_name: str
    result: Any
    is_error: bool = False


class ExecuteApprovedToolsInput(BaseModel):
    """Input for the execute_approved_tools_activity."""

    model_config = {"arbitrary_types_allowed": True}

    session_id: uuid.UUID
    workspace_id: uuid.UUID
    role: Role
    # Tools to execute
    approved_tools: list[ApprovedToolCall]
    denied_tools: list[DeniedToolCall]
    # Context needed for JWT minting
    allowed_actions: list[str]
    # Registry lock for action resolution
    registry_lock: RegistryLock


class ExecuteApprovedToolsResult(BaseModel):
    """Result from execute_approved_tools_activity."""

    results: list[ToolExecutionResult]
    success: bool = True
    error: str | None = None


HEARTBEAT_INTERVAL = 30  # seconds - must be less than heartbeat_timeout (60s)


async def _execute_action_with_heartbeat(
    action_name: str,
    args: dict[str, Any],
    claims: MCPTokenClaims,
    registry_lock: RegistryLock,
    tool_name: str,
) -> Any:
    """Execute an action with periodic heartbeats to Temporal.

    Wraps execute_action in a polling loop that sends heartbeats every
    HEARTBEAT_INTERVAL seconds to prevent Temporal from timing out the
    activity during long-running tool executions.

    Args:
        action_name: The normalized action name to execute.
        args: Arguments to pass to the action.
        claims: MCP token claims for authorization.
        registry_lock: Registry lock for action resolution.
        tool_name: Original tool name for heartbeat messages.

    Returns:
        The result from execute_action.

    Raises:
        ActionExecutionError: If the action execution fails.
        Exception: Any other error from the action.
    """
    # Create a task for the action execution
    action_task = asyncio.create_task(
        execute_action(
            action_name=action_name,
            args=args,
            claims=claims,
            registry_lock=registry_lock,
        )
    )

    elapsed = 0
    try:
        while True:
            try:
                # Wait for completion with heartbeat interval timeout
                result = await asyncio.wait_for(
                    asyncio.shield(action_task),
                    timeout=HEARTBEAT_INTERVAL,
                )
                return result
            except TimeoutError:
                # Action still running - send heartbeat and continue waiting
                elapsed += HEARTBEAT_INTERVAL
                activity.heartbeat(f"Executing tool {tool_name}: {elapsed}s elapsed")
                logger.debug(
                    "Heartbeat sent while executing tool",
                    tool_name=tool_name,
                    elapsed=elapsed,
                )
    finally:
        if not action_task.done():
            action_task.cancel()


@activity.defn
async def execute_approved_tools_activity(
    input: ExecuteApprovedToolsInput,
) -> ExecuteApprovedToolsResult:
    """Execute approved tools via MCP executor after approval.

    This activity:
    1. Mints a fresh JWT token (original may have expired during approval wait)
    2. Executes each approved tool via the MCP executor
    3. Returns results for approved tools and denial messages for denied tools

    The results are used by the workflow to construct a continuation message
    that includes tool results, allowing the agent to continue its response.

    Args:
        input: Contains approved/denied tools and context for execution.

    Returns:
        ExecuteApprovedToolsResult with tool execution results.
    """
    activity.heartbeat(f"Executing approved tools for session: {input.session_id}")

    results: list[ToolExecutionResult] = []

    # Ensure organization_id is set (agent sessions are always org-scoped)
    if input.role.organization_id is None:
        raise ValueError("organization_id is required for agent tool execution")

    # Build claims for execute_action calls
    claims = MCPTokenClaims(
        workspace_id=input.workspace_id,
        user_id=input.role.user_id,
        organization_id=input.role.organization_id,
        allowed_actions=input.allowed_actions,
        session_id=input.session_id,
    )

    logger.info(
        "Executing approved tools",
        session_id=str(input.session_id),
        approved_count=len(input.approved_tools),
        denied_count=len(input.denied_tools),
    )

    # Prefetch registry manifests into agent worker's cache for O(1) action resolution
    await registry_resolver.prefetch_lock(
        input.registry_lock, input.role.organization_id
    )

    # Initialize stream for emitting tool results to frontend
    stream = await AgentStream.new(
        session_id=input.session_id,
        workspace_id=input.workspace_id,
    )

    # Execute approved tools
    for tool_call in input.approved_tools:
        activity.heartbeat(f"Executing tool: {tool_call.tool_name}")

        try:
            # Normalize tool name (MCP format -> action name)
            action_name = normalize_mcp_tool_name(tool_call.tool_name)

            logger.info(
                "Executing approved tool",
                tool_call_id=tool_call.tool_call_id,
                tool_name=tool_call.tool_name,
                action_name=action_name,
            )

            # Execute the action via MCP executor with heartbeating
            result = await _execute_action_with_heartbeat(
                action_name=action_name,
                args=tool_call.args,
                claims=claims,
                registry_lock=input.registry_lock,
                tool_name=tool_call.tool_name,
            )

            tool_result = ToolExecutionResult(
                tool_call_id=tool_call.tool_call_id,
                tool_name=tool_call.tool_name,
                result=result,
                is_error=False,
            )
            results.append(tool_result)

            # Emit tool result to stream for frontend UI update
            await stream.append(
                UnifiedStreamEvent.tool_result_event(
                    tool_call_id=tool_result.tool_call_id,
                    tool_name=tool_result.tool_name,
                    output=tool_result.result,
                    is_error=tool_result.is_error,
                )
            )

            logger.info(
                "Tool executed successfully",
                tool_call_id=tool_call.tool_call_id,
                tool_name=tool_call.tool_name,
            )

        except ActionExecutionError as e:
            logger.error(
                "Tool execution failed",
                tool_call_id=tool_call.tool_call_id,
                tool_name=tool_call.tool_name,
                error=str(e),
            )
            tool_result = ToolExecutionResult(
                tool_call_id=tool_call.tool_call_id,
                tool_name=tool_call.tool_name,
                result=f"Tool execution failed: {e}",
                is_error=True,
            )
            results.append(tool_result)

            # Emit error result to stream
            await stream.append(
                UnifiedStreamEvent.tool_result_event(
                    tool_call_id=tool_result.tool_call_id,
                    tool_name=tool_result.tool_name,
                    output=tool_result.result,
                    is_error=tool_result.is_error,
                )
            )

        except Exception as e:
            logger.exception(
                "Unexpected error executing tool",
                tool_call_id=tool_call.tool_call_id,
                tool_name=tool_call.tool_name,
                error=str(e),
            )
            tool_result = ToolExecutionResult(
                tool_call_id=tool_call.tool_call_id,
                tool_name=tool_call.tool_name,
                result=f"Unexpected error: {e}",
                is_error=True,
            )
            results.append(tool_result)

            # Emit error result to stream
            await stream.append(
                UnifiedStreamEvent.tool_result_event(
                    tool_call_id=tool_result.tool_call_id,
                    tool_name=tool_result.tool_name,
                    output=tool_result.result,
                    is_error=tool_result.is_error,
                )
            )

    # Add denial results for rejected tools
    for denied_tool in input.denied_tools:
        tool_result = ToolExecutionResult(
            tool_call_id=denied_tool.tool_call_id,
            tool_name=denied_tool.tool_name,
            result=f"Tool denied by user: {denied_tool.reason}",
            is_error=True,
        )
        results.append(tool_result)

        # Emit denial result to stream
        await stream.append(
            UnifiedStreamEvent.tool_result_event(
                tool_call_id=tool_result.tool_call_id,
                tool_name=tool_result.tool_name,
                output=tool_result.result,
                is_error=tool_result.is_error,
            )
        )

    activity.heartbeat(f"Completed tool execution: {len(results)} results")

    # Replace interrupt entries with proper tool_result (atomic with tool execution)
    if results:
        async with AgentSessionService.with_session(role=input.role) as session_service:
            await session_service.replace_interrupt_with_tool_results(
                input.session_id,
                results,
            )

        activity.heartbeat("Replaced interrupt entries with tool results")

    return ExecuteApprovedToolsResult(results=results)
