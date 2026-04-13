from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import cast

import orjson
import pytest

import tracecat.agent.executor.activity as executor_activity
import tracecat.agent.sandbox.entrypoint as sandbox_entrypoint
from tracecat.agent.common.protocol import RuntimeInitPayload
from tracecat.agent.common.socket_io import MessageType
from tracecat.agent.common.types import SandboxAgentConfig
from tracecat.agent.executor.activity import AgentExecutorInput, SandboxedAgentExecutor
from tracecat.agent.executor.loopback import LoopbackResult
from tracecat.agent.sandbox.llm_proxy import LLM_SOCKET_NAME
from tracecat.agent.sandbox.nsjail import SpawnedRuntime
from tracecat.agent.types import AgentConfig
from tracecat.auth.types import Role


def _make_executor_input(*, enable_internet_access: bool) -> AgentExecutorInput:
    return AgentExecutorInput(
        session_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        user_prompt="hello",
        config=AgentConfig(
            model_name="gpt-5",
            model_provider="openai",
            enable_internet_access=enable_internet_access,
        ),
        role=Role(type="service", service_id="tracecat-agent-executor"),
        mcp_auth_token="mcp-token",
        llm_gateway_auth_token="llm-token",
    )


class _FakeLoopbackHandler:
    def __init__(self, input: object) -> None:
        self.input = input

    async def handle_connection(self, reader: object, writer: object) -> LoopbackResult:
        del reader, writer
        return LoopbackResult(success=True)

    async def emit_terminal_error(self, error_msg: str) -> None:
        raise AssertionError(f"unexpected terminal error: {error_msg}")


class _FakeServer:
    async def __aenter__(self) -> _FakeServer:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object,
    ) -> bool:
        del exc_type, exc, tb
        return False


class _FakeProcess:
    pid = 12345
    returncode = 0

    async def communicate(self) -> tuple[bytes, bytes]:
        return b"", b""

    async def wait(self) -> int:
        return 0

    def terminate(self) -> None:
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = -9


@pytest.mark.anyio
async def test_executor_skips_llm_socket_proxy_for_direct_litellm_runs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(executor_activity, "LoopbackHandler", _FakeLoopbackHandler)

    async def fake_create_job_directory(self: SandboxedAgentExecutor) -> Path:
        socket_dir = tmp_path / "sockets"
        socket_dir.mkdir(parents=True)
        return tmp_path

    async def fake_start_unix_server(
        callback: Callable[[object, object], Awaitable[None]],
        path: str,
    ) -> _FakeServer:
        del path
        await callback(object(), object())
        return _FakeServer()

    async def fake_spawn_jailed_runtime(**kwargs: object) -> SpawnedRuntime:
        assert kwargs["llm_socket_path"] is None
        return SpawnedRuntime(
            process=cast(asyncio.subprocess.Process, _FakeProcess()),
            job_dir=None,
        )

    monkeypatch.setattr(
        SandboxedAgentExecutor,
        "_create_job_directory",
        fake_create_job_directory,
    )
    monkeypatch.setattr(
        executor_activity.asyncio,
        "start_unix_server",
        fake_start_unix_server,
    )
    monkeypatch.setattr(
        executor_activity,
        "spawn_jailed_runtime",
        fake_spawn_jailed_runtime,
    )
    monkeypatch.setattr(
        SandboxedAgentExecutor,
        "_create_llm_socket_proxy",
        lambda self, socket_path: (_ for _ in ()).throw(
            AssertionError(f"unexpected LLM socket proxy for {socket_path}")
        ),
    )

    executor = SandboxedAgentExecutor(
        input=_make_executor_input(enable_internet_access=True),
    )
    result = await executor.run()

    assert result.success is True
    assert executor._llm_proxy is None


@pytest.mark.anyio
async def test_executor_starts_llm_socket_proxy_for_isolated_litellm_runs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(executor_activity, "LoopbackHandler", _FakeLoopbackHandler)

    created_socket_paths: list[Path] = []

    class _FakeProxy:
        started = False
        stopped = False

        async def start(self) -> None:
            self.started = True

        async def stop(self) -> None:
            self.stopped = True

    fake_proxy = _FakeProxy()

    async def fake_create_job_directory(self: SandboxedAgentExecutor) -> Path:
        socket_dir = tmp_path / "sockets"
        socket_dir.mkdir(parents=True)
        return tmp_path

    async def fake_start_unix_server(
        callback: Callable[[object, object], Awaitable[None]],
        path: str,
    ) -> _FakeServer:
        del path
        await callback(object(), object())
        return _FakeServer()

    async def fake_spawn_jailed_runtime(**kwargs: object) -> SpawnedRuntime:
        assert kwargs["llm_socket_path"] == tmp_path / "sockets" / LLM_SOCKET_NAME
        return SpawnedRuntime(
            process=cast(asyncio.subprocess.Process, _FakeProcess()),
            job_dir=None,
        )

    def fake_create_llm_socket_proxy(
        self: SandboxedAgentExecutor,
        socket_path: Path,
    ) -> _FakeProxy:
        created_socket_paths.append(socket_path)
        return fake_proxy

    monkeypatch.setattr(
        SandboxedAgentExecutor,
        "_create_job_directory",
        fake_create_job_directory,
    )
    monkeypatch.setattr(
        executor_activity.asyncio,
        "start_unix_server",
        fake_start_unix_server,
    )
    monkeypatch.setattr(
        executor_activity,
        "spawn_jailed_runtime",
        fake_spawn_jailed_runtime,
    )
    monkeypatch.setattr(
        SandboxedAgentExecutor,
        "_create_llm_socket_proxy",
        fake_create_llm_socket_proxy,
    )

    executor = SandboxedAgentExecutor(
        input=_make_executor_input(enable_internet_access=False),
    )
    result = await executor.run()

    assert result.success is True
    assert created_socket_paths == [tmp_path / "sockets" / LLM_SOCKET_NAME]
    assert fake_proxy.started is True
    assert fake_proxy.stopped is True


class _DummySocketStreamWriter:
    def __init__(self, writer: object) -> None:
        self.writer = writer

    async def send_error(self, error: str) -> None:
        raise AssertionError(f"unexpected send_error: {error}")

    async def send_done(self) -> None:
        raise AssertionError("unexpected send_done")

    async def close(self) -> None:
        return None


class _RecordingSocketStreamWriter:
    last_error: str | None = None
    done_sent: bool = False

    def __init__(self, writer: object) -> None:
        self.writer = writer

    async def send_error(self, error: str) -> None:
        type(self).last_error = error

    async def send_done(self) -> None:
        type(self).done_sent = True

    async def close(self) -> None:
        return None


class _DummyRuntime:
    last_payload: RuntimeInitPayload | None = None

    def __init__(self, socket_writer: object) -> None:
        self.socket_writer = socket_writer

    async def run(self, payload: RuntimeInitPayload) -> None:
        type(self).last_payload = payload


class _DummyBridge:
    instances: list[_DummyBridge] = []

    def __init__(self, socket_path: Path, port: int) -> None:
        self.socket_path = socket_path
        self.port = port
        self.started = False
        self.stopped = False
        type(self).instances.append(self)

    async def start(self) -> int:
        self.started = True
        return 4312

    async def stop(self) -> None:
        self.stopped = True


def _runtime_init_payload_bytes(*, enable_internet_access: bool) -> bytes:
    payload = RuntimeInitPayload(
        session_id=uuid.uuid4(),
        mcp_auth_token="mcp-token",
        llm_gateway_auth_token="llm-token",
        user_prompt="hello",
        config=SandboxAgentConfig(
            model_name="gpt-5",
            model_provider="openai",
            enable_internet_access=enable_internet_access,
        ),
    )
    return orjson.dumps(payload.to_dict())


@pytest.mark.anyio
async def test_sandbox_entrypoint_skips_bridge_for_direct_litellm_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRACECAT__LITELLM_BASE_URL", "http://litellm:4000")
    monkeypatch.delenv("TRACECAT__LLM_BRIDGE_PORT", raising=False)
    _DummyBridge.instances.clear()
    _DummyRuntime.last_payload = None

    async def fake_open_unix_connection(path: Path) -> tuple[object, object]:
        assert path == sandbox_entrypoint.TRACECAT__AGENT_CONTROL_SOCKET_PATH
        return object(), object()

    async def fake_read_message(
        reader: object,
        *,
        expected_type: MessageType,
    ) -> tuple[MessageType, bytes]:
        del reader
        assert expected_type is MessageType.INIT
        return expected_type, _runtime_init_payload_bytes(enable_internet_access=True)

    monkeypatch.setattr(
        sandbox_entrypoint.asyncio,
        "open_unix_connection",
        fake_open_unix_connection,
    )
    monkeypatch.setattr(
        sandbox_entrypoint,
        "SocketStreamWriter",
        _DummySocketStreamWriter,
    )
    monkeypatch.setattr(sandbox_entrypoint, "read_message", fake_read_message)
    monkeypatch.setattr(sandbox_entrypoint, "LLMBridge", _DummyBridge)
    monkeypatch.setattr(sandbox_entrypoint, "_load_runtime", lambda _: _DummyRuntime)

    await sandbox_entrypoint.run_sandboxed_runtime()

    assert _DummyBridge.instances == []
    assert "TRACECAT__LLM_BRIDGE_PORT" not in sandbox_entrypoint.os.environ
    assert _DummyRuntime.last_payload is not None
    assert _DummyRuntime.last_payload.config.enable_internet_access is True


@pytest.mark.anyio
async def test_sandbox_entrypoint_defaults_direct_litellm_url_for_internet_enabled_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TRACECAT__LITELLM_BASE_URL", raising=False)
    monkeypatch.delenv("TRACECAT__LLM_BRIDGE_PORT", raising=False)
    _DummyBridge.instances.clear()
    _DummyRuntime.last_payload = None
    monkeypatch.setattr(
        sandbox_entrypoint, "TRACECAT__LITELLM_BASE_URL", "http://127.0.0.1:4000"
    )

    async def fake_open_unix_connection(path: Path) -> tuple[object, object]:
        assert path == sandbox_entrypoint.TRACECAT__AGENT_CONTROL_SOCKET_PATH
        return object(), object()

    async def fake_read_message(
        reader: object,
        *,
        expected_type: MessageType,
    ) -> tuple[MessageType, bytes]:
        del reader
        assert expected_type is MessageType.INIT
        return expected_type, _runtime_init_payload_bytes(enable_internet_access=True)

    monkeypatch.setattr(
        sandbox_entrypoint.asyncio,
        "open_unix_connection",
        fake_open_unix_connection,
    )
    monkeypatch.setattr(
        sandbox_entrypoint, "SocketStreamWriter", _DummySocketStreamWriter
    )
    monkeypatch.setattr(sandbox_entrypoint, "read_message", fake_read_message)
    monkeypatch.setattr(sandbox_entrypoint, "LLMBridge", _DummyBridge)
    monkeypatch.setattr(sandbox_entrypoint, "_load_runtime", lambda _: _DummyRuntime)

    await sandbox_entrypoint.run_sandboxed_runtime()

    assert _DummyBridge.instances == []
    assert (
        sandbox_entrypoint.os.environ["TRACECAT__LITELLM_BASE_URL"]
        == "http://127.0.0.1:4000"
    )
    assert _DummyRuntime.last_payload is not None
    assert _DummyRuntime.last_payload.config.enable_internet_access is True


@pytest.mark.anyio
async def test_sandbox_entrypoint_starts_bridge_for_isolated_litellm_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRACECAT__LITELLM_BASE_URL", "http://litellm:4000")
    monkeypatch.delenv("TRACECAT__LLM_BRIDGE_PORT", raising=False)
    _DummyBridge.instances.clear()

    async def fake_open_unix_connection(path: Path) -> tuple[object, object]:
        assert path == sandbox_entrypoint.TRACECAT__AGENT_CONTROL_SOCKET_PATH
        return object(), object()

    async def fake_read_message(
        reader: object,
        *,
        expected_type: MessageType,
    ) -> tuple[MessageType, bytes]:
        del reader
        assert expected_type is MessageType.INIT
        return expected_type, _runtime_init_payload_bytes(enable_internet_access=False)

    monkeypatch.setattr(
        sandbox_entrypoint.asyncio,
        "open_unix_connection",
        fake_open_unix_connection,
    )
    monkeypatch.setattr(
        sandbox_entrypoint,
        "SocketStreamWriter",
        _DummySocketStreamWriter,
    )
    monkeypatch.setattr(sandbox_entrypoint, "read_message", fake_read_message)
    monkeypatch.setattr(sandbox_entrypoint, "LLMBridge", _DummyBridge)
    monkeypatch.setattr(sandbox_entrypoint, "_load_runtime", lambda _: _DummyRuntime)

    await sandbox_entrypoint.run_sandboxed_runtime()

    assert len(_DummyBridge.instances) == 1
    assert _DummyBridge.instances[0].started is True
    assert _DummyBridge.instances[0].stopped is True
    assert sandbox_entrypoint.os.environ["TRACECAT__LLM_BRIDGE_PORT"] == "4312"
