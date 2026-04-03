"""Worker-global broker for warm Claude runtime orchestration."""

from __future__ import annotations

import asyncio
import tempfile
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any

from tracecat.agent.common.config import TRACECAT__DISABLE_NSJAIL
from tracecat.agent.common.protocol import RuntimeEventEnvelope, RuntimeInitPayload
from tracecat.agent.common.stream_types import UnifiedStreamEvent
from tracecat.agent.executor.loopback import LoopbackHandler
from tracecat.agent.runtime.claude_code.runtime import (
    ClaudeAgentRuntime,
    RuntimeEventWriter,
)
from tracecat.agent.runtime.claude_code.transport import (
    ClaudeSandboxPathMapping,
    SandboxedCLITransport,
)
from tracecat.logger import logger


class LoopbackEnvelopeWriter(RuntimeEventWriter):
    """Adapter that wraps a LoopbackHandler as a RuntimeEventWriter.

    Constructs RuntimeEventEnvelope objects and passes them to the
    handler for dispatch.
    """

    def __init__(self, handler: LoopbackHandler) -> None:
        self._handler = handler

    async def send_stream_event(self, event: UnifiedStreamEvent) -> None:
        """Send a unified stream event."""
        await self._handler.process_envelope(
            RuntimeEventEnvelope.from_stream_event(event)
        )

    async def send_session_line(
        self, sdk_session_id: str, line: str, *, internal: bool = False
    ) -> None:
        """Send a raw Claude session line."""
        await self._handler.process_envelope(
            RuntimeEventEnvelope.from_session_line(
                sdk_session_id, line, internal=internal
            )
        )

    async def send_result(
        self,
        usage: dict[str, Any] | None = None,
        num_turns: int | None = None,
        duration_ms: int | None = None,
        output: Any = None,
    ) -> None:
        """Send the final Claude result."""
        await self._handler.process_envelope(
            RuntimeEventEnvelope.from_result(
                usage=usage,
                num_turns=num_turns,
                duration_ms=duration_ms,
                output=output,
            )
        )

    async def send_error(self, error: str) -> None:
        """Send a terminal runtime error."""
        await self._handler.process_envelope(RuntimeEventEnvelope.from_error(error))

    async def send_done(self) -> None:
        """Signal that the runtime turn is complete."""
        await self._handler.process_envelope(RuntimeEventEnvelope.done())

    async def send_log(self, level: str, message: str, **extra: object) -> None:
        """Send a structured runtime log event."""
        await self._handler.process_envelope(
            RuntimeEventEnvelope.from_log(level, message, dict(extra) or None)
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

        turn_started_at = perf_counter()

        def log_phase(phase: str, **extra: object) -> None:
            logger.info(
                "Agent benchmark phase",
                phase=phase,
                elapsed_ms=round((perf_counter() - turn_started_at) * 1000, 2),
                session_id=request.init_payload.session_id,
                execution_path="broker",
                component="broker",
                **extra,
            )

        try:
            log_phase("broker_turn_start")
            await handler.prepare()
            log_phase("broker_loopback_prepared")
            path_mapping = self._build_path_mapping(
                session_id=str(request.init_payload.session_id)
            )
            writer = LoopbackEnvelopeWriter(handler)
            runtime = ClaudeAgentRuntime(
                writer,
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
            log_phase(
                "broker_runtime_ready",
                runtime_cwd=str(path_mapping.runtime_cwd),
            )
            await runtime.run(request.init_payload)
            log_phase("broker_turn_complete")
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
