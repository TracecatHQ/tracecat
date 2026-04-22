from __future__ import annotations

import asyncio
import contextlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import orjson
import pytest

import tracecat.agent.executor.activity as executor_activity
import tracecat.agent.runtime.claude_code.broker as broker_module
import tracecat.agent.runtime.claude_code.runtime as runtime_module
import tracecat.agent.runtime.claude_code.session_paths as session_paths_module
import tracecat.agent.runtime.claude_code.transport as transport_module
import tracecat.agent.sandbox.nsjail as nsjail_module
import tracecat.agent.sandbox.shim_entrypoint as shim_entrypoint
from tracecat import config as app_config
from tracecat.agent.common.protocol import RuntimeInitPayload
from tracecat.agent.common.stream_types import (
    StreamEventType,
    ToolCallContent,
    UnifiedStreamEvent,
)
from tracecat.agent.executor.activity import (
    AgentExecutorInput,
    AgentExecutorResult,
    SandboxedAgentExecutor,
    run_agent_activity,
)
from tracecat.agent.executor.loopback import LoopbackHandler, LoopbackResult
from tracecat.agent.runtime.claude_code.broker import (
    ClaudeRuntimeBroker,
    ClaudeTurnRequest,
)
from tracecat.agent.runtime.claude_code.transport import SandboxedCLITransport
from tracecat.agent.sandbox.llm_proxy import LLM_SOCKET_NAME
from tracecat.agent.types import AgentConfig
from tracecat.auth.types import Role


@pytest.fixture(autouse=True, scope="session")
def default_org() -> Iterator[None]:
    yield


@pytest.fixture(autouse=True, scope="session")
def workflow_bucket() -> Iterator[None]:
    yield


@pytest.fixture(autouse=True)
def clean_redis_db() -> Iterator[None]:
    yield


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


def _set_disable_nsjail_mode(
    monkeypatch: pytest.MonkeyPatch,
    disable_nsjail: bool,
) -> None:
    monkeypatch.setattr(executor_activity, "TRACECAT__DISABLE_NSJAIL", disable_nsjail)
    monkeypatch.setattr(broker_module, "TRACECAT__DISABLE_NSJAIL", disable_nsjail)
    monkeypatch.setattr(nsjail_module, "TRACECAT__DISABLE_NSJAIL", disable_nsjail)
    monkeypatch.setattr(runtime_module, "TRACECAT__DISABLE_NSJAIL", disable_nsjail)


def _docker_nsjail_fallback_enabled() -> bool:
    return (
        os.environ.get("TRACECAT__AGENT_NSJAIL_DOCKER_FALLBACK_CHILD") != "1"
        and shutil.which("docker") is not None
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
    _set_disable_nsjail_mode(monkeypatch, disable_nsjail)
    return disable_nsjail


@pytest.fixture(
    params=[
        pytest.param(True, id="direct"),
        pytest.param(False, id="nsjail"),
    ]
)
def full_harness_disable_nsjail_mode(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> bool:
    disable_nsjail = bool(request.param)
    if (
        not disable_nsjail
        and not _agent_nsjail_available()
        and not _docker_nsjail_fallback_enabled()
    ):
        pytest.skip(
            "agent nsjail binary/rootfs unavailable on this host and Docker fallback is unavailable"
        )
    _set_disable_nsjail_mode(monkeypatch, disable_nsjail)
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
        self.requests: list[dict[str, Any]] = []
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
        body_bytes = b""
        if content_length:
            body_bytes = await reader.readexactly(content_length)

        self.request_count += 1
        try:
            request_body = json.loads(body_bytes.decode("utf-8")) if body_bytes else {}
        except json.JSONDecodeError:
            request_body = {}
        self.requests.append(request_body)

        body = json.dumps(
            {
                "id": f"msg_fake_{self.request_count}",
                "type": "message",
                "role": "assistant",
                "model": request_body.get("model") or "customer-alias",
                "content": [{"type": "text", "text": "fake claude response"}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {
                    "input_tokens": 1,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "output_tokens": 2,
                    "server_tool_use": None,
                    "service_tier": "standard",
                },
            },
            separators=(",", ":"),
        ).encode()
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


class _InMemoryStreamSink:
    def __init__(self) -> None:
        self.events: list[UnifiedStreamEvent] = []
        self.errors: list[str] = []
        self.done_count = 0

    async def append(self, event: UnifiedStreamEvent) -> None:
        self.events.append(event)

    async def error(self, error: str) -> None:
        self.errors.append(error)

    async def done(self) -> None:
        self.done_count += 1


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


class _FakeLoopbackRuntime:
    instances: list[_FakeLoopbackRuntime] = []
    payloads: list[RuntimeInitPayload] = []
    script: Callable[[LoopbackHandler, RuntimeInitPayload], Awaitable[None]] | None = (
        None
    )

    def __init__(
        self,
        handler: LoopbackHandler,
        *,
        transport_factory: Callable[[Any], SandboxedCLITransport],
        session_home_dir: Path,
        cwd: Path,
        cwd_setup_path: Path,
    ) -> None:
        del transport_factory
        self.handler = handler
        self.session_home_dir = session_home_dir
        self.cwd = cwd
        self.cwd_setup_path = cwd_setup_path
        type(self).instances.append(self)

    async def run(self, payload: RuntimeInitPayload) -> None:
        type(self).payloads.append(payload)
        script = type(self).script
        if script is None:
            raise AssertionError("Fake loopback runtime script was not configured")
        await script(self.handler, payload)


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


async def _run_full_claude_harness_runtime_case(
    *,
    disable_nsjail_mode: bool,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _FakeLLMSocketProxy.instances.clear()
    broker = ClaudeRuntimeBroker()
    await broker.start()
    stream_sink = _InMemoryStreamSink()
    persisted_session_lines: list[tuple[str, str, bool]] = []
    job_dir = Path(tempfile.mkdtemp(prefix="tcaj-", dir="/tmp"))

    async def fake_create_job_directory(self: SandboxedAgentExecutor) -> Path:
        del self
        socket_dir = job_dir / "sockets"
        socket_dir.mkdir(parents=True)
        return job_dir

    async def fake_initialize_stream_sink(self: LoopbackHandler) -> _InMemoryStreamSink:
        del self
        return stream_sink

    async def fake_persist_session_line(
        self: LoopbackHandler,
        sdk_session_id: str,
        session_line: str,
        *,
        internal: bool = False,
    ) -> None:
        del self
        persisted_session_lines.append((sdk_session_id, session_line, internal))

    monkeypatch.setattr(executor_activity, "LLMSocketProxy", _FakeLLMSocketProxy)
    monkeypatch.setattr(
        SandboxedAgentExecutor,
        "_create_job_directory",
        fake_create_job_directory,
    )
    monkeypatch.setattr(
        LoopbackHandler,
        "_initialize_stream_sink",
        fake_initialize_stream_sink,
    )
    monkeypatch.setattr(
        LoopbackHandler,
        "_persist_session_line",
        fake_persist_session_line,
    )
    monkeypatch.setattr(executor_activity, "get_claude_runtime_broker", lambda: broker)
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
    assert result.error is None
    assert result.output == "fake claude response"
    assert result.result_num_turns == 1
    assert result.messages == []

    assert len(_FakeLLMSocketProxy.instances) == 1
    proxy = _FakeLLMSocketProxy.instances[0]
    assert proxy.request_count >= 1
    assert proxy.started is True
    assert proxy.stopped is True
    assert any(request.get("model") == "customer-alias" for request in proxy.requests)

    assert stream_sink.errors == []
    assert stream_sink.done_count == 1


def _run_nsjail_harness_in_docker_or_skip() -> None:
    if os.environ.get("TRACECAT__AGENT_NSJAIL_DOCKER_FALLBACK_CHILD") == "1":
        pytest.skip("nsjail unavailable inside Docker fallback child")
    if shutil.which("docker") is None:
        pytest.skip("Docker CLI unavailable for nsjail fallback")

    docker_info = subprocess.run(
        ["docker", "info"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if docker_info.returncode != 0:
        pytest.skip("Docker daemon unavailable for nsjail fallback")

    repo_root = Path(__file__).resolve().parents[2]
    tests_mount = f"{repo_root / 'tests'}:/app/tests:ro"
    override_path = Path(
        tempfile.mkstemp(prefix="tracecat-agent-nsjail-test-", suffix=".yml")[1]
    )
    override_path.write_text(
        "\n".join(
            [
                "services:",
                "  api:",
                "    build:",
                "      target: test",
                "    cap_add:",
                "      - SYS_ADMIN",
                "    security_opt:",
                "      - seccomp:unconfined",
                "    volumes:",
                f"      - {json.dumps(tests_mount)}",
                "    environment:",
                '      TRACECAT__AGENT_NSJAIL_DOCKER_FALLBACK_CHILD: "1"',
                '      TRACECAT__DISABLE_NSJAIL: "false"',
                '      TRACECAT__SANDBOX_NSJAIL_PATH: "/usr/local/bin/nsjail"',
                '      TRACECAT__SANDBOX_ROOTFS_PATH: "/var/lib/tracecat/sandbox-rootfs"',
                '      PYTHONDONTWRITEBYTECODE: "1"',
                "",
            ]
        )
    )
    try:
        result = subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                str(repo_root / "docker-compose.dev.yml"),
                "-f",
                str(override_path),
                "run",
                "--rm",
                "--no-deps",
                "--build",
                "-T",
                "--entrypoint",
                "sh",
                "api",
                "-lc",
                "uv run python -m tests.unit.test_agent_sandbox_litellm --run-nsjail-harness-smoke",
            ],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
    finally:
        override_path.unlink(missing_ok=True)

    if result.returncode != 0:
        pytest.fail(
            "Dockerized nsjail harness fallback failed.\n\n"
            f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
        )


def _run_nsjail_harness_smoke_from_cli() -> None:
    async def run() -> None:
        monkeypatch = pytest.MonkeyPatch()
        tmp_path = Path(tempfile.mkdtemp(prefix="tracecat-agent-nsjail-smoke-"))
        try:
            _set_disable_nsjail_mode(monkeypatch, False)
            await _run_full_claude_harness_runtime_case(
                disable_nsjail_mode=False,
                monkeypatch=monkeypatch,
                tmp_path=tmp_path,
            )
        finally:
            monkeypatch.undo()
            shutil.rmtree(tmp_path, ignore_errors=True)

    asyncio.run(run())


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


async def _run_activity_with_fake_loopback_runtime(
    *,
    executor_input: AgentExecutorInput,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    script: Callable[[LoopbackHandler, RuntimeInitPayload], Awaitable[None]],
) -> tuple[
    AgentExecutorResult,
    _InMemoryStreamSink,
    list[tuple[str, str, bool]],
    _FakeProxy,
    list[RuntimeInitPayload],
    list[_FakeLoopbackRuntime],
]:
    _FakeLoopbackRuntime.instances.clear()
    _FakeLoopbackRuntime.payloads.clear()
    _FakeLoopbackRuntime.script = script

    stream_sink = _InMemoryStreamSink()
    persisted_session_lines: list[tuple[str, str, bool]] = []
    fake_proxy = _FakeProxy()
    broker = ClaudeRuntimeBroker()
    job_dir = tmp_path / "job"

    async def fake_create_job_directory(self: SandboxedAgentExecutor) -> Path:
        del self
        (job_dir / "sockets").mkdir(parents=True, exist_ok=True)
        return job_dir

    def fake_create_llm_socket_proxy(
        self: SandboxedAgentExecutor,
        socket_path: Path,
    ) -> _FakeProxy:
        del self, socket_path
        return fake_proxy

    async def fake_initialize_stream_sink(self: LoopbackHandler) -> _InMemoryStreamSink:
        del self
        return stream_sink

    async def fake_persist_session_line(
        self: LoopbackHandler,
        sdk_session_id: str,
        session_line: str,
        *,
        internal: bool = False,
    ) -> None:
        del self
        persisted_session_lines.append((sdk_session_id, session_line, internal))

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
        LoopbackHandler,
        "_initialize_stream_sink",
        fake_initialize_stream_sink,
    )
    monkeypatch.setattr(
        LoopbackHandler,
        "_persist_session_line",
        fake_persist_session_line,
    )
    monkeypatch.setattr(executor_activity, "get_claude_runtime_broker", lambda: broker)
    monkeypatch.setattr(broker_module, "ClaudeAgentRuntime", _FakeLoopbackRuntime)
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

    await broker.start()
    try:
        result = await run_agent_activity(executor_input)
    finally:
        await broker.stop()
        _FakeLoopbackRuntime.script = None
        shutil.rmtree(job_dir, ignore_errors=True)

    return (
        result,
        stream_sink,
        persisted_session_lines,
        fake_proxy,
        list(_FakeLoopbackRuntime.payloads),
        list(_FakeLoopbackRuntime.instances),
    )


@pytest.mark.anyio
async def test_run_agent_activity_with_fake_runtime_exercises_loopback_approval_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _set_disable_nsjail_mode(monkeypatch, True)
    executor_input = _make_executor_input(enable_internet_access=False)

    async def approval_script(
        handler: LoopbackHandler,
        payload: RuntimeInitPayload,
    ) -> None:
        assert payload.session_id == executor_input.session_id
        assert payload.is_fork is False
        assert payload.is_approval_continuation is False
        await handler.send_stream_event(
            UnifiedStreamEvent.approval_request_event(
                [
                    ToolCallContent(
                        id="call_approval",
                        name="core__http_request",
                        input={"url": "https://example.com", "method": "GET"},
                    )
                ]
            )
        )
        await handler.send_stream_event(
            UnifiedStreamEvent.tool_result_event(
                tool_call_id="call_approval",
                tool_name="core__http_request",
                output=["Request interrupted by user"],
                is_error=True,
            )
        )
        await handler.send_done()

    (
        result,
        stream_sink,
        persisted_session_lines,
        fake_proxy,
        payloads,
        runtimes,
    ) = await _run_activity_with_fake_loopback_runtime(
        executor_input=executor_input,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        script=approval_script,
    )

    assert result.success is True
    assert result.error is None
    assert result.approval_requested is True
    assert result.approval_items == [
        ToolCallContent(
            id="call_approval",
            name="core__http_request",
            input={"url": "https://example.com", "method": "GET"},
        )
    ]
    assert result.messages == []

    assert [event.type for event in stream_sink.events] == [
        StreamEventType.APPROVAL_REQUEST
    ]
    assert stream_sink.errors == []
    assert stream_sink.done_count == 1
    assert persisted_session_lines == []

    assert fake_proxy.started is True
    assert fake_proxy.stopped is True
    assert len(payloads) == 1
    assert len(runtimes) == 1


@pytest.mark.parametrize(
    ("disable_nsjail", "is_fork", "is_approval_continuation"),
    [
        pytest.param(True, True, False, id="fork-direct"),
        pytest.param(False, True, False, id="fork-nsjail"),
        pytest.param(True, False, True, id="approval-continuation-direct"),
    ],
)
@pytest.mark.anyio
async def test_run_agent_activity_with_fake_runtime_plumbs_resume_flags_to_loopback(
    disable_nsjail: bool,
    is_fork: bool,
    is_approval_continuation: bool,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _set_disable_nsjail_mode(monkeypatch, disable_nsjail)
    executor_input = _make_executor_input(enable_internet_access=False).model_copy(
        update={
            "sdk_session_id": "parent-sdk-session",
            "sdk_session_data": '{"type":"user","message":{"content":"parent"}}\n',
            "is_fork": is_fork,
            "is_approval_continuation": is_approval_continuation,
        }
    )
    session_line = (
        '{"uuid":"line-1","type":"assistant","message":{"role":"assistant",'
        '"content":[{"type":"text","text":"resumed"}]}}\n'
    )

    async def resume_script(
        handler: LoopbackHandler,
        payload: RuntimeInitPayload,
    ) -> None:
        assert payload.sdk_session_id == "parent-sdk-session"
        assert payload.sdk_session_data == executor_input.sdk_session_data
        assert payload.is_fork is is_fork
        assert payload.is_approval_continuation is is_approval_continuation
        await handler.send_stream_event(
            UnifiedStreamEvent(
                type=StreamEventType.TEXT_DELTA,
                part_id=0,
                text="resumed",
            )
        )
        await handler.send_session_line("child-sdk-session", session_line)
        await handler.send_result(
            usage={"input_tokens": 3, "output_tokens": 5},
            num_turns=2,
            duration_ms=10,
            output={"status": "continued"},
        )
        await handler.send_done()

    (
        result,
        stream_sink,
        persisted_session_lines,
        _fake_proxy,
        payloads,
        runtimes,
    ) = await _run_activity_with_fake_loopback_runtime(
        executor_input=executor_input,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        script=resume_script,
    )

    assert result.success is True
    assert result.error is None
    assert result.output == {"status": "continued"}
    assert result.result_usage == {"input_tokens": 3, "output_tokens": 5}
    assert result.result_num_turns == 2
    assert result.messages == []

    assert [event.type for event in stream_sink.events] == [StreamEventType.TEXT_DELTA]
    assert stream_sink.errors == []
    assert stream_sink.done_count == 1
    assert persisted_session_lines == [
        ("child-sdk-session", session_line, False),
    ]

    assert len(payloads) == 1
    assert payloads[0].is_fork is is_fork
    assert payloads[0].is_approval_continuation is is_approval_continuation
    assert len(runtimes) == 1
    if disable_nsjail:
        assert runtimes[0].cwd == runtimes[0].cwd_setup_path
        assert runtimes[0].cwd.is_relative_to(tmp_path / "sessions")
    else:
        assert runtimes[0].cwd == Path("/work/claude-project")
        assert runtimes[0].cwd_setup_path.is_relative_to(tmp_path / "sessions")


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
async def test_run_agent_activity_spawns_full_claude_harness_runtime_in_each_sandbox_mode(
    full_harness_disable_nsjail_mode: bool,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    if not full_harness_disable_nsjail_mode and not _agent_nsjail_available():
        _run_nsjail_harness_in_docker_or_skip()
        return

    await _run_full_claude_harness_runtime_case(
        disable_nsjail_mode=full_harness_disable_nsjail_mode,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
    )


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


if __name__ == "__main__":
    if sys.argv[1:] == ["--run-nsjail-harness-smoke"]:
        _run_nsjail_harness_smoke_from_cli()
    else:
        raise SystemExit(
            "Usage: python -m tests.unit.test_agent_sandbox_litellm "
            "--run-nsjail-harness-smoke"
        )
