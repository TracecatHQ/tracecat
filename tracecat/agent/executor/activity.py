"""Temporal activities for agent runtime execution."""

from __future__ import annotations

import asyncio
import contextlib
import shutil
import tempfile
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Any

from pydantic import AliasChoices, BaseModel, Field
from temporalio import activity

from tracecat import config as app_config
from tracecat.agent.common.config import (
    TRACECAT__AGENT_SANDBOX_MEMORY_MB,
    TRACECAT__AGENT_SANDBOX_TIMEOUT,
    TRACECAT__DISABLE_NSJAIL,
)
from tracecat.agent.common.exceptions import AgentSandboxExecutionError
from tracecat.agent.common.protocol import RuntimeInitPayload, RuntimeToolResult
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
from tracecat.agent.runtime_services import get_claude_runtime_broker
from tracecat.agent.sandbox.llm_proxy import LLM_SOCKET_NAME, LLMSocketProxy
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

from .schemas import (
    ApprovedToolCall,
    DeniedToolCall,
    ToolExecutionResult,
    serialize_tool_result_content,
)


class AgentExecutorInput(BaseModel):
    """Input for the agent executor activity.

    On resume after approval, sdk_session_data ends at the assistant tool_use
    and approval_tool_results carries the user tool_result input.
    """

    # ``extra="ignore"`` keeps Temporal activity replay working after the
    # legacy ``use_workspace_credentials`` field was removed: activity input
    # stored in history still carries the old key and pydantic will silently
    # drop it instead of raising.
    model_config = {"arbitrary_types_allowed": True, "extra": "ignore"}

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
    # Session resume data from previous runs
    sdk_session_id: str | None = None
    sdk_session_data: str | None = None
    # True when resuming after an approval decision.
    is_approval_continuation: bool = False
    # Approved or denied tool results to send as the next Claude SDK input.
    approval_tool_results: list[ToolExecutionResult] = Field(default_factory=list)
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
    """Executes an agent turn through the worker-global runtime broker.

    This executor:
    1. Creates a job directory with Unix sockets
    2. Starts the host-side LLM proxy
    3. Dispatches the turn to the runtime broker
    4. Cleans up on completion
    """

    input: AgentExecutorInput
    timeout_seconds: int = field(
        default_factory=lambda: TRACECAT__AGENT_SANDBOX_TIMEOUT
    )
    memory_mb: int = field(default_factory=lambda: TRACECAT__AGENT_SANDBOX_MEMORY_MB)

    # Internal state
    _job_dir: Path | None = field(default=None, init=False, repr=False)
    _llm_proxy: LLMSocketProxy | None = field(default=None, init=False, repr=False)
    _fatal_error: str | None = field(default=None, init=False, repr=False)
    _fatal_error_event: asyncio.Event = field(
        default_factory=asyncio.Event, init=False, repr=False
    )
    _turn_started_at: float = field(
        default_factory=perf_counter, init=False, repr=False
    )

    def _log_benchmark_phase(self, phase: str, **extra: object) -> None:
        """Emit a temporary structured benchmark log for this turn."""
        logger.info(
            "Agent benchmark phase",
            phase=phase,
            elapsed_ms=round((perf_counter() - self._turn_started_at) * 1000, 2),
            session_id=self.input.session_id,
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
            model_provider=self.input.config.model_provider,
            catalog_id=self.input.config.catalog_id,
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
            approval_tool_results=[
                RuntimeToolResult(
                    tool_call_id=result.tool_call_id,
                    content=serialize_tool_result_content(result.result),
                    is_error=result.is_error,
                )
                for result in self.input.approval_tool_results
            ],
            is_fork=self.input.is_fork,
        )

    async def run(self) -> AgentExecutorResult:
        """Execute the agent through the brokered runtime.

        Returns:
            AgentExecutorResult with success status and any session updates.
        """
        result = AgentExecutorResult(success=False)
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
            )
            handler = LoopbackHandler(input=loopback_input)

            llm_socket_path = socket_dir / LLM_SOCKET_NAME
            self._llm_proxy = self._create_llm_socket_proxy(llm_socket_path)

            await self._run_with_broker(
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
            skills_dir=self._skills_dir(),
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

    async def _create_job_directory(self) -> Path:
        """Create a temporary job directory with socket subdirectory."""
        job_id = str(self.input.session_id)[:12]
        # Hardcoded job socket directory for per-job control sockets
        base_dir = Path("/tmp/tracecat-agent-jobs")
        base_dir.mkdir(parents=True, exist_ok=True)

        job_dir = Path(tempfile.mkdtemp(prefix=f"agent-job-{job_id}-", dir=base_dir))
        try:
            socket_dir = job_dir / "sockets"
            socket_dir.mkdir(mode=0o700)
            skills_dir = job_dir / "home" / ".claude" / "skills"
            skills_dir.mkdir(parents=True, exist_ok=True)
            await self._stage_resolved_skills(skills_dir)

            # Note: The MCP socket directory is mounted directly into NSJail at /mcp-sockets
            # so we don't need to symlink it here
            logger.debug(
                "Created job directory",
                job_dir=str(job_dir),
                socket_dir=str(socket_dir),
            )
            return job_dir
        except BaseException:
            await asyncio.to_thread(shutil.rmtree, job_dir, True)
            raise

    def _skills_dir(self) -> Path | None:
        """Return the per-run staged skills directory."""
        if self._job_dir is None:
            return None
        skills_dir = self._job_dir / "home" / ".claude" / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)
        return skills_dir

    async def _stage_resolved_skills(self, skills_dir: Path) -> None:
        """Stage resolved published skills into the per-run home directory."""

        resolved_skills = self.input.config.resolved_skills or []
        if not resolved_skills:
            return
        duplicate_names = sorted(
            name
            for name, count in Counter(
                resolved_skill.skill_name for resolved_skill in resolved_skills
            ).items()
            if count > 1
        )
        if duplicate_names:
            raise ValueError(
                f"Resolved preset contains duplicate skill names: {duplicate_names}"
            )

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
                    skills_dir / resolved_skill.skill_name,
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
            max_concurrent_downloads = max(
                1,
                min(
                    len(version_files),
                    TRACECAT__AGENT_SKILL_CACHE_MAX_CONCURRENT_DOWNLOADS,
                ),
            )
            semaphore = asyncio.Semaphore(max_concurrent_downloads)

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
                await asyncio.to_thread(shutil.rmtree, self._job_dir)
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
    """Temporal activity that runs one brokered agent turn.

    This activity:
    1. Creates a SandboxedAgentExecutor
    2. Dispatches the turn through the worker-global runtime broker
    3. Returns the result with session updates

    The broker-owned transport decides whether the runtime shim runs with nsjail
    or as a direct subprocess based on TRACECAT__DISABLE_NSJAIL.

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
