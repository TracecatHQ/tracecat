"""Temporal activities for agent runtime execution."""

from __future__ import annotations

import asyncio
import contextlib
import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Any

import orjson
from pydantic import AliasChoices, BaseModel, Field
from temporalio import activity

from tracecat import config as app_config
from tracecat.agent.common.config import (
    TRACECAT__AGENT_SANDBOX_MEMORY_MB,
    TRACECAT__AGENT_SANDBOX_TIMEOUT,
    TRACECAT__DISABLE_NSJAIL,
)
from tracecat.agent.common.exceptions import AgentSandboxExecutionError
from tracecat.agent.common.protocol import RuntimeInitPayload
from tracecat.agent.common.socket_io import MAX_PAYLOAD_SIZE
from tracecat.agent.common.stream_types import ToolCallContent
from tracecat.agent.common.types import MCPToolDefinition, SandboxAgentConfig
from tracecat.agent.executor.loopback import (
    LoopbackHandler,
    LoopbackInput,
    LoopbackResult,
)
from tracecat.agent.runtime.claude_code.broker import (
    ClaudeTurnRequest,
    ConcurrentSessionTurnError,
)
from tracecat.agent.runtime.claude_code.session_paths import (
    build_claude_sandbox_path_mapping,
)
from tracecat.agent.runtime_services import get_claude_runtime_broker
from tracecat.agent.sandbox.llm_proxy import LLM_SOCKET_NAME, LLMSocketProxy
from tracecat.agent.sandbox.nsjail import (
    SpawnedRuntime,
    cleanup_spawned_runtime,
    spawn_jailed_runtime,
)
from tracecat.agent.session.service import AgentSessionService
from tracecat.agent.types import AgentConfig
from tracecat.auth.types import Role
from tracecat.chat.schemas import ChatMessage
from tracecat.feature_flags import is_feature_enabled
from tracecat.feature_flags.enums import FeatureFlag
from tracecat.logger import logger
from tracecat.registry.lock.types import RegistryLock

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
    # Credential scope used by the LLM proxy in passthrough mode to fetch the
    # customer's upstream API key. Not a secret.
    use_workspace_credentials: bool = False


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
    _spawned_runtime: SpawnedRuntime | None = field(
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
    _turn_started_at: float = field(
        default_factory=perf_counter, init=False, repr=False
    )
    _execution_path: str = field(default="legacy", init=False, repr=False)

    INIT_PAYLOAD_FILENAME = "init.json"

    def _log_benchmark_phase(self, phase: str, **extra: object) -> None:
        """Emit a temporary structured benchmark log for this turn."""
        logger.info(
            "Agent benchmark phase",
            phase=phase,
            elapsed_ms=round((perf_counter() - self._turn_started_at) * 1000, 2),
            session_id=self.input.session_id,
            execution_path=self._execution_path,
            sandbox_mode="direct" if TRACECAT__DISABLE_NSJAIL else "nsjail",
            **extra,
        )

    def _create_llm_socket_proxy(self, socket_path: Path) -> LLMSocketProxy:
        """Create the host-side LiteLLM transport proxy for this execution."""

        def on_error(error_msg: str) -> None:
            self._fatal_error = error_msg
            self._fatal_error_event.set()

        if self.input.config.passthrough:
            if self.input.config.base_url is None:
                raise AgentSandboxExecutionError(
                    "Custom model provider passthrough requires a resolved base_url."
                )
            upstream_url = self.input.config.base_url
        else:
            upstream_url = app_config.TRACECAT__LITELLM_BASE_URL

        logger.info(
            "Creating LLM socket proxy",
            has_upstream_url=bool(upstream_url),
            passthrough=self.input.config.passthrough,
        )

        return LLMSocketProxy(
            socket_path=socket_path,
            upstream_url=upstream_url,
            on_error=on_error,
            passthrough=self.input.config.passthrough,
            role=self.input.role,
            use_workspace_credentials=self.input.use_workspace_credentials,
            model_provider=self.input.config.model_provider,
        )

    def _build_runtime_init_payload(self) -> RuntimeInitPayload:
        """Build the runtime init payload for this execution."""
        return RuntimeInitPayload(
            session_id=self.input.session_id,
            mcp_auth_token=self.input.mcp_auth_token,
            config=SandboxAgentConfig.from_agent_config(self.input.config),
            user_prompt=self.input.user_prompt,
            llm_gateway_auth_token=self.input.llm_gateway_auth_token,
            allowed_actions=self.input.allowed_actions,
            sdk_session_id=self.input.sdk_session_id,
            sdk_session_data=self.input.sdk_session_data,
            is_approval_continuation=self.input.is_approval_continuation,
            is_fork=self.input.is_fork,
        )

    async def _write_runtime_init_payload(self, payload: RuntimeInitPayload) -> Path:
        """Write the runtime init payload atomically into the job directory."""
        if self._job_dir is None:
            raise RuntimeError("Job directory must exist before writing init payload")

        init_path = self._job_dir / self.INIT_PAYLOAD_FILENAME
        temp_path = init_path.with_suffix(".tmp")
        payload_bytes = orjson.dumps(payload.to_dict())
        if len(payload_bytes) > MAX_PAYLOAD_SIZE:
            raise AgentSandboxExecutionError(
                "Runtime init payload exceeds max size "
                f"({len(payload_bytes)} > {MAX_PAYLOAD_SIZE} bytes)"
            )

        def _write() -> None:
            temp_path.write_bytes(payload_bytes)
            temp_path.replace(init_path)

        await asyncio.to_thread(_write)
        logger.debug(
            "Wrote runtime init payload",
            init_path=str(init_path),
            payload_size=len(payload_bytes),
            session_id=self.input.session_id,
        )
        return init_path

    async def run(self) -> AgentExecutorResult:
        """Execute the agent in an NSJail sandbox.

        Returns:
            AgentExecutorResult with success status and any session updates.
        """
        result = AgentExecutorResult(success=False)
        self._execution_path = (
            "broker"
            if is_feature_enabled(FeatureFlag.AGENT_SANDBOX_BROKER)
            else "legacy"
        )
        self._log_benchmark_phase("activity_start")

        try:
            # Create job directory with sockets
            self._job_dir = await self._create_job_directory()
            socket_dir = self._job_dir / "sockets"
            init_payload = self._build_runtime_init_payload()
            self._log_benchmark_phase(
                "job_dir_ready",
                job_dir=str(self._job_dir),
                socket_dir=str(socket_dir),
            )

            # Create loopback handler
            loopback_input = LoopbackInput(
                session_id=self.input.session_id,
                workspace_id=self.input.workspace_id,
                execution_path=self._execution_path,
            )
            handler = LoopbackHandler(input=loopback_input)

            llm_socket_path = socket_dir / LLM_SOCKET_NAME
            self._llm_proxy = self._create_llm_socket_proxy(llm_socket_path)

            if is_feature_enabled(FeatureFlag.AGENT_SANDBOX_BROKER):
                await self._run_with_broker(
                    result=result,
                    handler=handler,
                    init_payload=init_payload,
                    socket_dir=socket_dir,
                    llm_socket_path=llm_socket_path,
                )
                return result
            await self._run_with_nsjail(
                result=result,
                handler=handler,
                init_payload=init_payload,
                socket_dir=socket_dir,
                llm_socket_path=llm_socket_path,
            )

        except AgentSandboxExecutionError as e:
            logger.error("Agent sandbox execution failed", error=str(e))
            result.error = str(e)
        except Exception as e:
            logger.exception("Unexpected error in agent executor", error=str(e))
            result.error = f"Unexpected error: {e}"
        finally:
            await self._cleanup()

        return result

    @staticmethod
    def _apply_loopback_result(
        result: AgentExecutorResult, loopback_result: LoopbackResult
    ) -> None:
        """Copy loopback result fields into the activity result."""
        result.success = loopback_result.success
        result.error = loopback_result.error
        result.approval_requested = loopback_result.approval_requested
        result.approval_items = loopback_result.approval_items or None
        result.output = loopback_result.output
        result.result_usage = loopback_result.result_usage
        result.result_num_turns = loopback_result.result_num_turns

    async def _run_with_broker(
        self,
        *,
        result: AgentExecutorResult,
        handler: LoopbackHandler,
        init_payload: RuntimeInitPayload,
        socket_dir: Path,
        llm_socket_path: Path,
    ) -> None:
        """Execute the Claude turn through the worker-global warm broker."""
        if self._job_dir is None:
            raise RuntimeError("Job directory must exist before broker execution")

        broker = get_claude_runtime_broker()

        if self._llm_proxy is None:
            raise RuntimeError("LLM proxy must exist before broker startup")
        await self._llm_proxy.start()
        logger.info(
            "Started LLM socket proxy",
            socket_path=str(llm_socket_path),
        )
        self._log_benchmark_phase("broker_llm_proxy_ready")

        request = ClaudeTurnRequest(
            init_payload=init_payload,
            job_dir=self._job_dir,
            socket_dir=socket_dir,
            llm_socket_path=llm_socket_path,
            enable_internet_access=init_payload.config.enable_internet_access,
        )
        broker_task = asyncio.create_task(broker.run_turn(request, handler))
        self._log_benchmark_phase("broker_turn_dispatched")

        async def wait_fatal_error() -> str:
            await self._fatal_error_event.wait()
            return self._fatal_error or "Unknown LLM error"

        fatal_error_task = asyncio.create_task(wait_fatal_error())
        heartbeat_interval = 30
        elapsed = 0

        try:
            while elapsed < self.timeout_seconds:
                done, _ = await asyncio.wait(
                    [broker_task, fatal_error_task],
                    timeout=heartbeat_interval,
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if not done:
                    elapsed += heartbeat_interval
                    activity.heartbeat(
                        f"Agent running: {self.input.session_id} ({elapsed}s elapsed)"
                    )
                    continue

                if fatal_error_task in done:
                    error_msg = fatal_error_task.result()
                    result.error = error_msg
                    await broker.cancel_turn(str(self.input.session_id))
                    await handler.emit_terminal_error(error_msg)
                    break

                await broker_task
                self._apply_loopback_result(result, handler.build_result())
                self._log_benchmark_phase(
                    "broker_activity_complete",
                    success=result.success,
                    approval_requested=result.approval_requested,
                )
                break
            else:
                result.error = (
                    f"Agent execution timed out after {self.timeout_seconds}s"
                )
                await broker.cancel_turn(str(self.input.session_id))
                await handler.emit_terminal_error(result.error)
        except Exception as e:
            result.error = str(e)
            await handler.emit_terminal_error(result.error)
            if not isinstance(e, ConcurrentSessionTurnError):
                raise
        except asyncio.CancelledError:
            await broker.cancel_turn(str(self.input.session_id))
            raise
        finally:
            for task in (fatal_error_task, broker_task):
                if not task.done():
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task

    async def _run_with_nsjail(
        self,
        *,
        result: AgentExecutorResult,
        handler: LoopbackHandler,
        init_payload: RuntimeInitPayload,
        socket_dir: Path,
        llm_socket_path: Path,
    ) -> None:
        """Execute the Claude turn through the legacy socket-based sandbox path."""
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

        control_socket_path = socket_dir / "control.sock"
        logger.info(
            "Starting control socket server",
            socket_path=str(control_socket_path),
        )

        async def start_control_socket_server() -> asyncio.Server:
            server = await asyncio.start_unix_server(
                connection_callback,
                path=str(control_socket_path),
            )
            control_socket_path.chmod(0o600)
            return server

        async def start_llm_proxy() -> None:
            if self._llm_proxy is None:
                raise RuntimeError("LLM proxy must exist before startup")
            await self._llm_proxy.start()
            logger.info(
                "Started LLM socket proxy",
                socket_path=str(llm_socket_path),
            )

        server_task: asyncio.Task[asyncio.Server] | None = None
        try:
            async with asyncio.TaskGroup() as tg:
                init_payload_task = tg.create_task(
                    self._write_runtime_init_payload(init_payload)
                )
                llm_proxy_task = tg.create_task(start_llm_proxy())
                server_task = tg.create_task(start_control_socket_server())
        except* Exception:
            if (
                server_task is not None
                and server_task.done()
                and not server_task.cancelled()
            ):
                with contextlib.suppress(Exception):
                    server = server_task.result()
                    server.close()
                    await server.wait_closed()
            raise

        init_payload_path = init_payload_task.result()
        _ = llm_proxy_task.result()
        server = server_task.result()
        self._log_benchmark_phase(
            "legacy_runtime_bootstrap_ready",
            init_payload_path=str(init_payload_path),
        )

        async with server:
            self._log_benchmark_phase("legacy_sandbox_spawn_start")
            path_mapping = build_claude_sandbox_path_mapping(
                session_id=str(init_payload.session_id),
                disable_nsjail=TRACECAT__DISABLE_NSJAIL,
            )
            runtime_result = await spawn_jailed_runtime(
                socket_dir=socket_dir,
                llm_socket_path=llm_socket_path,
                init_payload_path=init_payload_path,
                session_home_dir=path_mapping.host_home_dir,
                session_project_dir=path_mapping.host_project_dir,
                enable_internet_access=init_payload.config.enable_internet_access,
            )
            self._spawned_runtime = runtime_result
            self._process = runtime_result.process
            self._log_benchmark_phase(
                "legacy_sandbox_spawned",
                pid=self._process.pid,
            )
            logger.info(
                "Agent runtime process spawned",
                pid=self._process.pid,
                session_id=self.input.session_id,
                mode="direct" if TRACECAT__DISABLE_NSJAIL else "nsjail",
            )

            heartbeat_interval = 30
            elapsed = 0

            async def wait_fatal_error() -> str:
                await self._fatal_error_event.wait()
                return self._fatal_error or "Unknown LLM error"

            fatal_error_task = asyncio.create_task(wait_fatal_error())

            async def wait_process_exit() -> tuple[int, str]:
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

                    if fatal_error_task in done:
                        error_msg = fatal_error_task.result()
                        logger.error(
                            "Fatal LLM error detected, terminating agent",
                            error=error_msg,
                        )
                        result.error = error_msg
                        if self._process and self._process.returncode is None:
                            self._process.kill()
                        await handler.emit_terminal_error(error_msg)
                        break

                    if process_exit_task in done and not self._loopback_result.done():
                        returncode, stderr = process_exit_task.result()
                        if returncode == 0:
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
                                await handler.emit_terminal_error(error_msg)
                                break

                            logger.info(
                                "Loopback result received after clean runtime exit",
                                success=loopback_result.success,
                                error=loopback_result.error,
                            )
                            self._apply_loopback_result(result, loopback_result)
                            self._log_benchmark_phase(
                                "legacy_activity_complete",
                                success=result.success,
                                approval_requested=result.approval_requested,
                            )
                            break

                        if stderr:
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
                        await handler.emit_terminal_error(error_msg)
                        break

                    loopback_result = self._loopback_result.result()
                    logger.info(
                        "Loopback result received",
                        success=loopback_result.success,
                        error=loopback_result.error,
                    )
                    self._apply_loopback_result(result, loopback_result)
                    self._log_benchmark_phase(
                        "legacy_activity_complete",
                        success=result.success,
                        approval_requested=result.approval_requested,
                    )
                    break
                else:
                    logger.error("Agent execution timed out waiting for loopback")
                    result.error = (
                        f"Agent execution timed out after {self.timeout_seconds}s"
                    )
                    await handler.emit_terminal_error(result.error)

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
                for task in [fatal_error_task, process_exit_task]:
                    if not task.done():
                        task.cancel()
                        try:
                            await task
                        except asyncio.CancelledError:
                            pass

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

        if self._spawned_runtime:
            try:
                cleanup_spawned_runtime(self._spawned_runtime)
            except Exception as e:
                logger.warning("Failed to clean up spawned runtime", error=str(e))
            self._spawned_runtime = None

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
