from __future__ import annotations

import uuid
from pathlib import Path

import orjson
import pytest

import tracecat.agent.executor.activity as executor_activity
import tracecat.agent.sandbox.shim_entrypoint as shim_entrypoint
from tracecat.agent.executor.activity import (
    AgentExecutorInput,
    AgentExecutorResult,
    SandboxedAgentExecutor,
)
from tracecat.agent.executor.loopback import LoopbackResult
from tracecat.agent.runtime.claude_code.broker import ClaudeTurnRequest
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
