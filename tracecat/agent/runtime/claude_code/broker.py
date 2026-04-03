"""Worker-global broker for warm Claude runtime orchestration."""

from __future__ import annotations

import asyncio
import tempfile
from dataclasses import dataclass
from pathlib import Path

from tracecat.agent.common.config import TRACECAT__DISABLE_NSJAIL
from tracecat.agent.common.protocol import RuntimeInitPayload
from tracecat.agent.executor.loopback import LoopbackHandler
from tracecat.agent.runtime.claude_code.runtime import ClaudeAgentRuntime
from tracecat.agent.runtime.claude_code.transport import (
    ClaudeSandboxPathMapping,
    SandboxedCLITransport,
)


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


class ClaudeRuntimeBroker:
    """Warm in-process broker that owns host-side Claude orchestration."""

    def __init__(self) -> None:
        self._closed = False
        self._active_turns: dict[str, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """Mark the broker ready for new turns."""
        self._closed = False

    async def stop(self) -> None:
        """Cancel any remaining active turns and reject future work."""
        self._closed = True
        async with self._lock:
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
        if self._closed:
            raise RuntimeError("Claude runtime broker is not running")

        session_key = str(request.init_payload.session_id)
        current_task = asyncio.current_task()
        if current_task is None:
            raise RuntimeError("Broker turn must run inside an asyncio task")

        async with self._lock:
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
            runtime = ClaudeAgentRuntime(
                handler,
                transport_factory=lambda options: SandboxedCLITransport(
                    options=options,
                    socket_dir=request.socket_dir,
                    llm_socket_path=request.llm_socket_path,
                    job_dir=request.job_dir,
                    path_mapping=path_mapping,
                    enable_internet_access=request.enable_internet_access,
                    session_id=str(request.init_payload.session_id),
                ),
                session_home_dir=path_mapping.host_home_dir,
                cwd=path_mapping.runtime_cwd,
                cwd_setup_path=path_mapping.host_project_dir,
            )
            await runtime.run(request.init_payload)
        finally:
            async with self._lock:
                self._active_turns.pop(session_key, None)

    async def cancel_turn(self, session_id: str) -> None:
        """Cancel an active turn for the provided session, if one exists."""
        async with self._lock:
            task = self._active_turns.get(session_id)
        if task is not None:
            task.cancel()

    @staticmethod
    def _build_path_mapping(*, session_id: str) -> ClaudeSandboxPathMapping:
        """Build stable host/runtime path mapping for one Claude session.

        The broker path must preserve Claude's home/project directories across turns
        so `--resume <sdk_session_id>` can find the same session metadata and JSONL
        history on resumed turns.
        """
        session_root = Path(tempfile.gettempdir()) / f"tracecat-agent-{session_id}"
        host_home_dir = session_root / "claude-home"
        host_project_dir = session_root / "claude-project"
        host_home_dir.mkdir(parents=True, exist_ok=True)
        host_project_dir.mkdir(parents=True, exist_ok=True)

        if TRACECAT__DISABLE_NSJAIL:
            runtime_home_dir = host_home_dir
            runtime_cwd = host_project_dir
        else:
            runtime_home_dir = Path("/work/claude-home")
            runtime_cwd = Path("/work/claude-project")

        return ClaudeSandboxPathMapping(
            host_home_dir=host_home_dir,
            host_project_dir=host_project_dir,
            runtime_home_dir=runtime_home_dir,
            runtime_cwd=runtime_cwd,
        )
