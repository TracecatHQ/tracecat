"""Worker-global broker for warm Claude runtime orchestration."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from tracecat.agent.artifacts.providers import get_artifact_working_set_provider
from tracecat.agent.artifacts.working_set import (
    ArtifactWorkingSetContext,
    ArtifactWorkingSetInput,
)
from tracecat.agent.common.config import TRACECAT__DISABLE_NSJAIL
from tracecat.agent.common.protocol import RuntimeInitPayload
from tracecat.agent.executor.loopback import LoopbackHandler
from tracecat.agent.runtime.claude_code.runtime import ClaudeAgentRuntime
from tracecat.agent.runtime.claude_code.transport import (
    SandboxedCLITransport,
)
from tracecat.agent.runtime.session_paths import (
    AgentSandboxPathMapping,
    build_agent_sandbox_path_mapping,
)
from tracecat.logger import logger


class ConcurrentSessionTurnError(RuntimeError):
    """Raised when a second concurrent turn is started for the same session."""


@dataclass(frozen=True, slots=True)
class ClaudeTurnRequest:
    """All inputs needed for one broker-managed Claude turn."""

    init_payload: RuntimeInitPayload
    job_dir: Path
    socket_dir: Path
    llm_socket_path: Path
    enable_internet_access: bool
    artifact_working_set: ArtifactWorkingSetInput | None = None
    skills_dir: Path | None = None
    hydrate_work_dir: Callable[[Path], Awaitable[None]] | None = None


class ClaudeRuntimeBroker:
    """Warm in-process broker that owns host-side Claude orchestration."""

    def __init__(self) -> None:
        self._closed = False
        self._leased_sessions: set[str] = set()
        self._active_turns: dict[str, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """Mark the broker ready for new turns."""
        self._closed = False

    async def stop(self) -> None:
        """Cancel any remaining active turns and reject future work."""
        async with self._lock:
            self._closed = True
            active_tasks = list(self._active_turns.values())
        for task in active_tasks:
            task.cancel()
        for task in active_tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def run_turn(
        self,
        request: ClaudeTurnRequest,
        handler: LoopbackHandler,
    ) -> None:
        """Run one Claude turn through the warm broker."""
        session_key = str(request.init_payload.session_id)
        async with self.session_turn_lease(session_key):
            await self.run_turn_in_session_lease(request, handler)

    @asynccontextmanager
    async def session_turn_lease(self, session_id: str) -> AsyncIterator[None]:
        """Hold the worker-local same-session turn lease.

        The lease covers broker execution plus any activity-owned post-turn work
        that must finish before the next same-session turn can start.
        """
        if asyncio.current_task() is None:
            raise RuntimeError("Session turn lease must run inside an asyncio task")

        async with self._lock:
            if self._closed:
                raise RuntimeError("Claude runtime broker is not running")
            if session_id in self._leased_sessions:
                raise ConcurrentSessionTurnError(
                    f"Session {session_id} already has an active turn"
                )
            self._leased_sessions.add(session_id)

        try:
            yield
        finally:
            async with self._lock:
                self._leased_sessions.discard(session_id)

    async def run_turn_in_session_lease(
        self,
        request: ClaudeTurnRequest,
        handler: LoopbackHandler,
    ) -> None:
        """Run one Claude turn while the caller holds ``session_turn_lease``."""
        session_key = str(request.init_payload.session_id)
        current_task = asyncio.current_task()
        if current_task is None:
            raise RuntimeError("Broker turn must run inside an asyncio task")

        async with self._lock:
            if self._closed:
                raise RuntimeError("Claude runtime broker is not running")
            if session_key not in self._leased_sessions:
                raise RuntimeError("Session turn lease must be held before run_turn")
            if session_key in self._active_turns:
                raise ConcurrentSessionTurnError(
                    f"Session {session_key} already has an active turn"
                )
            self._active_turns[session_key] = current_task

        try:
            await handler.prepare()
            path_mapping = self._build_path_mapping(
                session_id=str(request.init_payload.session_id)
            )
            if request.hydrate_work_dir is not None:
                await request.hydrate_work_dir(path_mapping.host_work_dir)

            artifact_prompt_fragment = None
            if request.artifact_working_set is not None:
                provider = get_artifact_working_set_provider()
                working_set = await provider.prepare_turn(
                    ArtifactWorkingSetContext(
                        session_id=request.init_payload.session_id,
                        workspace_id=request.artifact_working_set.workspace_id,
                        role=request.artifact_working_set.role,
                        artifacts=request.artifact_working_set.artifacts,
                        host_work_dir=path_mapping.host_work_dir,
                        runtime_work_dir=path_mapping.runtime_work_dir,
                    )
                )
                artifact_prompt_fragment = working_set.prompt_fragment
                logger.info(
                    "Prepared artifact prompt fragment for Claude runtime",
                    session_id=str(request.init_payload.session_id),
                    artifact_count=len(working_set.manifest.artifacts),
                    artifact_root=working_set.manifest.root,
                )
            runtime = ClaudeAgentRuntime(
                handler,
                transport_factory=lambda options: SandboxedCLITransport(
                    options=options,
                    socket_dir=request.socket_dir,
                    llm_socket_path=request.llm_socket_path,
                    job_dir=request.job_dir,
                    path_mapping=path_mapping,
                    enable_internet_access=request.enable_internet_access,
                    use_jailed_paths=not TRACECAT__DISABLE_NSJAIL,
                    session_id=str(request.init_payload.session_id),
                    skills_dir=request.skills_dir,
                ),
                session_home_dir=path_mapping.host_home_dir,
                cwd=path_mapping.runtime_work_dir,
                cwd_setup_path=path_mapping.host_work_dir,
                system_prompt_fragments=(artifact_prompt_fragment,)
                if artifact_prompt_fragment
                else (),
            )
            await runtime.run(request.init_payload)
        finally:
            async with self._lock:
                if self._active_turns.get(session_key) is current_task:
                    self._active_turns.pop(session_key, None)

    async def cancel_turn(self, session_id: str) -> None:
        """Cancel an active turn for the provided session, if one exists."""
        async with self._lock:
            task = self._active_turns.get(session_id)
        if task is not None:
            task.cancel()

    @staticmethod
    def _build_path_mapping(*, session_id: str) -> AgentSandboxPathMapping:
        """Build stable host/runtime path mapping for one Claude session.

        The broker path must preserve the agent home and work dir across turns
        so harness-specific resume metadata and JSONL history remain stable.
        """
        return build_agent_sandbox_path_mapping(
            session_id=session_id,
            disable_nsjail=TRACECAT__DISABLE_NSJAIL,
        )
