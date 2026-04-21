from __future__ import annotations

import asyncio
import contextlib
import os
import platform
import shutil
import sys
import tempfile
import uuid
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import orjson
import pytest

import tracecat.agent.executor.activity as executor_activity
import tracecat.agent.runtime.claude_code.broker as broker_module
import tracecat.agent.runtime.claude_code.session_paths as session_paths_module
import tracecat.agent.runtime.claude_code.transport as transport_module
import tracecat.agent.sandbox.nsjail as nsjail_module
import tracecat.agent.sandbox.shim_entrypoint as shim_entrypoint
from tracecat import config as app_config
from tracecat.agent.executor.activity import (
    AgentExecutorInput,
    AgentExecutorResult,
    SandboxedAgentExecutor,
    run_agent_activity,
)
from tracecat.agent.executor.loopback import LoopbackResult
from tracecat.agent.runtime.claude_code.broker import (
    ClaudeRuntimeBroker,
    ClaudeTurnRequest,
)
from tracecat.agent.runtime.claude_code.transport import SandboxedCLITransport
from tracecat.agent.sandbox.llm_proxy import LLM_SOCKET_NAME
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


def _make_passthrough_executor_input(
    *, enable_internet_access: bool, base_url: str = "https://customer-litellm.example"
) -> AgentExecutorInput:
    return AgentExecutorInput(
        session_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        user_prompt="hello",
        config=AgentConfig(
            model_name="customer-alias",
            model_provider="custom-model-provider",
            base_url=base_url,
            passthrough=True,
            enable_internet_access=enable_internet_access,
        ),
        role=Role(type="service", service_id="tracecat-agent-executor"),
        mcp_auth_token="mcp-token",
        llm_gateway_auth_token="llm-token",
    )


def _agent_nsjail_available() -> bool:
    nsjail_path = Path(app_config.TRACECAT__SANDBOX_NSJAIL_PATH)
    rootfs_path = Path(app_config.TRACECAT__SANDBOX_ROOTFS_PATH)
    return (
        platform.system() == "Linux"
        and nsjail_path.is_file()
        and os.access(nsjail_path, os.X_OK)
        and rootfs_path.is_dir()
    )


@pytest.fixture(
    params=[
        pytest.param(True, id="direct"),
        pytest.param(
            False,
            id="nsjail",
            marks=pytest.mark.skipif(
                not _agent_nsjail_available(),
                reason="agent nsjail binary/rootfs unavailable on this host",
            ),
        ),
    ]
)
def disable_nsjail_mode(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> bool:
    disable_nsjail = bool(request.param)
    monkeypatch.setattr(executor_activity, "TRACECAT__DISABLE_NSJAIL", disable_nsjail)
    monkeypatch.setattr(broker_module, "TRACECAT__DISABLE_NSJAIL", disable_nsjail)
    monkeypatch.setattr(nsjail_module, "TRACECAT__DISABLE_NSJAIL", disable_nsjail)
    return disable_nsjail


class _FakeLoopbackHandler:
    def __init__(self, input: object) -> None:
        self.input = input
        self.prepared = False

    async def prepare(self) -> None:
        self.prepared = True

    async def handle_connection(self, reader: object, writer: object) -> LoopbackResult:
        del reader, writer
        return LoopbackResult(success=True)

    def build_result(self) -> LoopbackResult:
        return LoopbackResult(success=True)

    async def emit_terminal_error(self, error_msg: str) -> None:
        raise AssertionError(f"unexpected terminal error: {error_msg}")


class _FakeBroker:
    def __init__(self) -> None:
        self.requests: list[ClaudeTurnRequest] = []
        self.cancelled_session_ids: list[str] = []

    async def run_turn(
        self,
        request: ClaudeTurnRequest,
        handler: _FakeLoopbackHandler,
    ) -> None:
        self.requests.append(request)
        await handler.prepare()

    async def cancel_turn(self, session_id: str) -> None:
        self.cancelled_session_ids.append(session_id)


class _FakeProxy:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True


class _FakeLLMSocketProxy:
    instances: list[_FakeLLMSocketProxy] = []

    def __init__(
        self,
        *,
        socket_path: Path,
        upstream_url: str,
        on_error: Callable[[str], None] | None = None,
        passthrough: bool = False,
        role: object | None = None,
        use_workspace_credentials: bool = False,
        model_provider: str | None = None,
    ) -> None:
        del on_error, role
        self.socket_path = socket_path
        self.upstream_url = upstream_url
        self.passthrough = passthrough
        self.use_workspace_credentials = use_workspace_credentials
        self.model_provider = model_provider
        self.started = False
        self.stopped = False
        self.request_count = 0
        self._server: asyncio.Server | None = None
        type(self).instances.append(self)

    async def start(self) -> None:
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(FileNotFoundError):
            self.socket_path.unlink()
        self._server = await asyncio.start_unix_server(
            self._handle_connection,
            path=str(self.socket_path),
        )
        self.started = True

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        with contextlib.suppress(FileNotFoundError):
            self.socket_path.unlink()
        self.stopped = True

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        headers = b""
        while True:
            line = await reader.readline()
            if not line:
                return
            headers += line
            if line == b"\r\n":
                break

        content_length = 0
        for line in headers.split(b"\r\n"):
            if line.lower().startswith(b"content-length:"):
                content_length = int(line.split(b":", 1)[1].strip())
                break
        if content_length:
            await reader.readexactly(content_length)

        self.request_count += 1
        body = b'{"ok":true}'
        writer.write(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/json\r\n"
            + f"Content-Length: {len(body)}\r\n".encode()
            + b"\r\n"
            + body
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()


class _FakeRuntimeConnectingTransport:
    instances: list[_FakeRuntimeConnectingTransport] = []

    def __init__(
        self,
        _handler: object,
        *,
        transport_factory: Callable[[Any], SandboxedCLITransport],
        session_home_dir: Path,
        cwd: Path,
        cwd_setup_path: Path,
    ) -> None:
        self.transport_factory = transport_factory
        self.session_home_dir = session_home_dir
        self.cwd = cwd
        self.cwd_setup_path = cwd_setup_path
        self.transport: SandboxedCLITransport | None = None
        type(self).instances.append(self)

    async def run(self, payload: object) -> None:
        options = SimpleNamespace(
            env={"ANTHROPIC_AUTH_TOKEN": "fake-llm-token"},
            enable_file_checkpointing=False,
            stderr=None,
        )
        transport = self.transport_factory(options)
        self.transport = transport
        await transport.connect()
        await transport.close()


class _FakeAgentSessionService:
    async def __aenter__(self) -> _FakeAgentSessionService:
        return self

    async def __aexit__(
        self,
        exc_type: object,
        exc: object,
        tb: object,
    ) -> None:
        del exc_type, exc, tb

    async def list_messages(self, _session_id: uuid.UUID) -> list[object]:
        return []


async def _run_executor_with_fake_broker(
    *,
    executor_input: AgentExecutorInput,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[AgentExecutorResult, list[Path], _FakeProxy, _FakeBroker]:
    monkeypatch.setattr(executor_activity, "LoopbackHandler", _FakeLoopbackHandler)

    created_socket_paths: list[Path] = []
    fake_proxy = _FakeProxy()
    fake_broker = _FakeBroker()

    async def fake_create_job_directory(self: SandboxedAgentExecutor) -> Path:
        del self
        socket_dir = tmp_path / "sockets"
        socket_dir.mkdir(parents=True)
        return tmp_path

    def fake_create_llm_socket_proxy(
        self: SandboxedAgentExecutor,
        socket_path: Path,
    ) -> _FakeProxy:
        del self
        created_socket_paths.append(socket_path)
        return fake_proxy

    monkeypatch.setattr(
        SandboxedAgentExecutor,
        "_create_job_directory",
        fake_create_job_directory,
    )
    monkeypatch.setattr(
        SandboxedAgentExecutor,
        "_create_llm_socket_proxy",
        fake_create_llm_socket_proxy,
    )
    monkeypatch.setattr(
        executor_activity,
        "get_claude_runtime_broker",
        lambda: fake_broker,
    )

    executor = SandboxedAgentExecutor(input=executor_input)
    result = await executor.run()

    return result, created_socket_paths, fake_proxy, fake_broker


@pytest.mark.anyio
async def test_run_agent_activity_with_fake_litellm_provider_spawns_runtime_in_each_sandbox_mode(
    disable_nsjail_mode: bool,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _FakeLLMSocketProxy.instances.clear()
    _FakeRuntimeConnectingTransport.instances.clear()
    broker = ClaudeRuntimeBroker()
    await broker.start()
    job_dir = Path(tempfile.mkdtemp(prefix="tc-agent-"))

    async def fake_create_job_directory(self: SandboxedAgentExecutor) -> Path:
        del self
        (job_dir / "sockets").mkdir(parents=True)
        return job_dir

    async def fake_build_claude_command(self: SandboxedCLITransport) -> list[str]:
        del self
        python_bin = sys.executable if disable_nsjail_mode else "/usr/local/bin/python3"
        code = ";".join(
            [
                "import os, sys, urllib.request",
                "req = urllib.request.Request("
                "os.environ['ANTHROPIC_BASE_URL'] + '/v1/messages', "
                "data=b'{}', "
                "headers={'Content-Type': 'application/json'}, "
                "method='POST')",
                "urllib.request.urlopen(req, timeout=5).read()",
                "sys.stdin.buffer.read()",
            ]
        )
        return [python_bin, "-c", code]

    monkeypatch.setattr(executor_activity, "LoopbackHandler", _FakeLoopbackHandler)
    monkeypatch.setattr(executor_activity, "LLMSocketProxy", _FakeLLMSocketProxy)
    monkeypatch.setattr(
        SandboxedAgentExecutor,
        "_create_job_directory",
        fake_create_job_directory,
    )
    monkeypatch.setattr(executor_activity, "get_claude_runtime_broker", lambda: broker)
    monkeypatch.setattr(
        broker_module,
        "ClaudeAgentRuntime",
        _FakeRuntimeConnectingTransport,
    )
    monkeypatch.setattr(
        transport_module.SandboxedCLITransport,
        "_build_claude_command",
        fake_build_claude_command,
    )
    monkeypatch.setattr(
        session_paths_module.tempfile,
        "gettempdir",
        lambda: str(tmp_path / "sessions"),
    )
    monkeypatch.setattr(
        executor_activity,
        "activity",
        SimpleNamespace(heartbeat=lambda _message: None),
    )
    monkeypatch.setattr(
        executor_activity.AgentSessionService,
        "with_session",
        lambda **_kwargs: _FakeAgentSessionService(),
    )

    try:
        result = await run_agent_activity(
            _make_passthrough_executor_input(enable_internet_access=False),
        )
    finally:
        await broker.stop()
        shutil.rmtree(job_dir, ignore_errors=True)

    assert result.success is True
    assert len(_FakeLLMSocketProxy.instances) == 1
    proxy = _FakeLLMSocketProxy.instances[0]
    assert proxy.started is True
    assert proxy.stopped is True
    assert proxy.upstream_url == "https://customer-litellm.example"
    assert proxy.passthrough is True
    assert proxy.model_provider == "custom-model-provider"
    assert proxy.request_count == 1

    assert len(_FakeRuntimeConnectingTransport.instances) == 1
    runtime = _FakeRuntimeConnectingTransport.instances[0]
    assert runtime.transport is not None
    if disable_nsjail_mode:
        assert runtime.cwd == runtime.cwd_setup_path
        assert runtime.cwd.is_relative_to(tmp_path / "sessions")
    else:
        assert runtime.cwd == Path("/work/claude-project")


@pytest.mark.anyio
async def test_executor_always_starts_llm_socket_proxy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The executor always creates the LLM socket proxy, even with internet access enabled."""
    (
        result,
        created_socket_paths,
        fake_proxy,
        fake_broker,
    ) = await _run_executor_with_fake_broker(
        executor_input=_make_executor_input(enable_internet_access=True),
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
    )

    assert result.success is True
    assert created_socket_paths == [tmp_path / "sockets" / LLM_SOCKET_NAME]
    assert fake_proxy.started is True
    assert fake_proxy.stopped is True
    assert len(fake_broker.requests) == 1
    assert fake_broker.requests[0].llm_socket_path == (
        tmp_path / "sockets" / LLM_SOCKET_NAME
    )
    assert fake_broker.requests[0].enable_internet_access is True


@pytest.mark.anyio
async def test_executor_starts_llm_socket_proxy_for_isolated_passthrough_runs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (
        result,
        created_socket_paths,
        fake_proxy,
        fake_broker,
    ) = await _run_executor_with_fake_broker(
        executor_input=_make_executor_input(enable_internet_access=False),
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
    )

    assert result.success is True
    assert created_socket_paths == [tmp_path / "sockets" / LLM_SOCKET_NAME]
    assert fake_proxy.started is True
    assert fake_proxy.stopped is True
    assert len(fake_broker.requests) == 1
    assert fake_broker.requests[0].llm_socket_path == (
        tmp_path / "sockets" / LLM_SOCKET_NAME
    )
    assert fake_broker.requests[0].enable_internet_access is False


@pytest.mark.anyio
async def test_executor_starts_llm_socket_proxy_for_passthrough_provider_with_internet_enabled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (
        result,
        created_socket_paths,
        fake_proxy,
        fake_broker,
    ) = await _run_executor_with_fake_broker(
        executor_input=_make_passthrough_executor_input(enable_internet_access=True),
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
    )

    assert result.success is True
    assert created_socket_paths == [tmp_path / "sockets" / LLM_SOCKET_NAME]
    assert fake_proxy.started is True
    assert fake_proxy.stopped is True
    assert len(fake_broker.requests) == 1
    assert fake_broker.requests[0].llm_socket_path == (
        tmp_path / "sockets" / LLM_SOCKET_NAME
    )
    assert fake_broker.requests[0].enable_internet_access is True


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


class _FakeProcess:
    stdin = object()
    stdout = object()
    stderr = object()
    returncode = 0

    async def wait(self) -> int:
        return self.returncode

    def terminate(self) -> None:
        self.returncode = -15


@pytest.mark.anyio
async def test_sandbox_shim_starts_bridge_and_sets_child_base_url(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _DummyBridge.instances.clear()
    captured: dict[str, object] = {}

    init_path = tmp_path / "shim-init.json"
    init_path.write_bytes(
        orjson.dumps(
            {
                "command": ["claude", "--print"],
                "env": {"ANTHROPIC_AUTH_TOKEN": "llm-token"},
                "cwd": str(tmp_path),
            }
        )
    )
    monkeypatch.setenv(shim_entrypoint.INIT_PAYLOAD_ENV_VAR, str(init_path))
    monkeypatch.setenv(
        shim_entrypoint.LLM_SOCKET_ENV_VAR,
        str(tmp_path / "llm.sock"),
    )

    async def fake_create_subprocess_exec(*args: str, **kwargs: object) -> _FakeProcess:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _FakeProcess()

    monkeypatch.setattr(
        shim_entrypoint.asyncio,
        "create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    monkeypatch.setattr(shim_entrypoint, "LLMBridge", _DummyBridge)

    async def fake_pump_stream(*_args: object) -> None:
        return None

    async def fake_pump_stdin_to_process(_stdin: object) -> None:
        return None

    monkeypatch.setattr(shim_entrypoint, "_pump_stream", fake_pump_stream)
    monkeypatch.setattr(
        shim_entrypoint,
        "_pump_stdin_to_process",
        fake_pump_stdin_to_process,
    )

    await shim_entrypoint.run_sandboxed_claude_shim()

    assert len(_DummyBridge.instances) == 1
    assert _DummyBridge.instances[0].started is True
    assert _DummyBridge.instances[0].stopped is True
    assert captured["args"] == ("claude", "--print")
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    child_env = kwargs["env"]
    assert isinstance(child_env, dict)
    assert child_env["ANTHROPIC_AUTH_TOKEN"] == "llm-token"
    assert child_env["TRACECAT__LLM_BRIDGE_PORT"] == "4312"
    assert child_env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:4312"
