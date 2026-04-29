from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from claude_agent_sdk import ClaudeAgentOptions
from claude_agent_sdk.types import (
    AgentDefinition,
    McpHttpServerConfig,
    McpServerConfig,
    McpStdioServerConfig,
)

from tracecat.agent.common.protocol import RuntimeInitPayload
from tracecat.agent.common.types import SandboxAgentConfig
from tracecat.agent.runtime.claude_code import broker as broker_module
from tracecat.agent.runtime.claude_code import session_paths as session_paths_module
from tracecat.agent.runtime.claude_code import transport as transport_module
from tracecat.agent.runtime.claude_code.broker import (
    ClaudeRuntimeBroker,
    ClaudeTurnRequest,
    ConcurrentSessionTurnError,
)
from tracecat.agent.runtime.claude_code.session_paths import ClaudeSandboxPathMapping
from tracecat.agent.runtime.claude_code.transport import SandboxedCLITransport


def _make_request(tmp_path: Path) -> ClaudeTurnRequest:
    agent_config = SandboxAgentConfig(
        model_name="claude-3-5-sonnet-20241022",
        model_provider="anthropic",
    )
    job_dir = tmp_path / "job"
    socket_dir = job_dir / "sockets"
    socket_dir.mkdir(parents=True)
    return ClaudeTurnRequest(
        init_payload=RuntimeInitPayload(
            session_id=uuid4(),
            mcp_auth_token="mcp-token",
            config=agent_config,
            user_prompt="hello",
            llm_gateway_auth_token="llm-token",
        ),
        job_dir=job_dir,
        socket_dir=socket_dir,
        llm_socket_path=socket_dir / "llm.sock",
        enable_internet_access=False,
    )


def _make_transport(tmp_path: Path, *, use_jailed_paths: bool) -> SandboxedCLITransport:
    runtime_root = Path("/work") if use_jailed_paths else tmp_path / "runtime"
    path_mapping = ClaudeSandboxPathMapping(
        host_home_dir=tmp_path / "home",
        host_project_dir=tmp_path / "project",
        runtime_home_dir=runtime_root / "home",
        runtime_cwd=runtime_root / "project",
    )
    return SandboxedCLITransport(
        options=ClaudeAgentOptions(),
        session_id="session-123",
        socket_dir=tmp_path / "sockets",
        llm_socket_path=tmp_path / "llm.sock",
        job_dir=tmp_path,
        path_mapping=path_mapping,
        enable_internet_access=False,
        use_jailed_paths=use_jailed_paths,
    )


def _trusted_mcp_config(port: int, token: str = "token") -> McpHttpServerConfig:
    return {
        "type": "http",
        "url": f"http://127.0.0.1:{port}/mcp",
        "headers": {"Authorization": f"Bearer {token}"},
    }


def _stdio_mcp_config(command: str) -> McpStdioServerConfig:
    return {"type": "stdio", "command": command}


@pytest.mark.anyio
async def test_broker_rejects_second_turn_for_same_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    release = asyncio.Event()

    class FakeRuntime:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def run(self, _payload: RuntimeInitPayload) -> None:
            await release.wait()

    monkeypatch.setattr(broker_module, "ClaudeAgentRuntime", FakeRuntime)

    broker = ClaudeRuntimeBroker()
    await broker.start()

    request = _make_request(tmp_path)
    handler = cast(
        Any,
        SimpleNamespace(
            prepare=AsyncMock(),
            process_envelope=AsyncMock(),
        ),
    )

    first_task = asyncio.create_task(broker.run_turn(request, handler))
    await asyncio.sleep(0)

    with pytest.raises(ConcurrentSessionTurnError):
        await broker.run_turn(request, handler)

    release.set()
    await first_task


@pytest.mark.anyio
async def test_broker_rechecks_closed_state_after_waiting_for_lock(
    tmp_path: Path,
) -> None:
    broker = ClaudeRuntimeBroker()
    await broker.start()
    request = _make_request(tmp_path)
    handler = cast(
        Any,
        SimpleNamespace(
            prepare=AsyncMock(),
            process_envelope=AsyncMock(),
        ),
    )

    await broker._lock.acquire()
    task = asyncio.create_task(broker.run_turn(request, handler))
    await asyncio.sleep(0)
    broker._closed = True
    broker._lock.release()

    with pytest.raises(RuntimeError, match="Claude runtime broker is not running"):
        await task


def test_build_path_mapping_uses_runtime_mount_paths_when_nsjail_enabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(broker_module, "TRACECAT__DISABLE_NSJAIL", False)
    monkeypatch.setattr(
        session_paths_module.tempfile, "gettempdir", lambda: str(tmp_path)
    )

    mapping = ClaudeRuntimeBroker._build_path_mapping(session_id="session-123")

    assert (
        mapping.host_home_dir == tmp_path / "tracecat-agent-session-123" / "claude-home"
    )
    assert (
        mapping.host_project_dir
        == tmp_path / "tracecat-agent-session-123" / "claude-project"
    )
    assert mapping.runtime_home_dir == Path("/work/claude-home")
    assert mapping.runtime_cwd == Path("/work/claude-project")


def test_build_path_mapping_uses_host_paths_in_direct_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(broker_module, "TRACECAT__DISABLE_NSJAIL", True)
    monkeypatch.setattr(
        session_paths_module.tempfile, "gettempdir", lambda: str(tmp_path)
    )

    mapping = ClaudeRuntimeBroker._build_path_mapping(session_id="session-123")

    assert mapping.runtime_home_dir == mapping.host_home_dir
    assert mapping.runtime_cwd == mapping.host_project_dir


def test_build_path_mapping_is_stable_per_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(broker_module, "TRACECAT__DISABLE_NSJAIL", True)
    monkeypatch.setattr(
        session_paths_module.tempfile, "gettempdir", lambda: str(tmp_path)
    )

    first = ClaudeRuntimeBroker._build_path_mapping(session_id="session-123")
    second = ClaudeRuntimeBroker._build_path_mapping(session_id="session-123")

    assert first == second


def test_transport_rewrites_bundled_claude_path_for_jail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tracecat.agent.runtime.claude_code.transport.claude_agent_sdk.__file__",
        "/app/.venv/lib/python3.12/site-packages/claude_agent_sdk/__init__.py",
    )

    command = [
        "/app/.venv/lib/python3.12/site-packages/claude_agent_sdk/_bundled/claude",
        "--print",
    ]

    rewritten = SandboxedCLITransport._rewrite_command_for_jail(command)

    assert rewritten == [
        "/site-packages/claude_agent_sdk/_bundled/claude",
        "--print",
    ]


def test_transport_keeps_bundled_claude_path_for_direct_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tracecat.agent.runtime.claude_code.transport.claude_agent_sdk.__file__",
        "/app/.venv/lib/python3.12/site-packages/claude_agent_sdk/__init__.py",
    )

    command = [
        "/app/.venv/lib/python3.12/site-packages/claude_agent_sdk/_bundled/claude",
        "--print",
    ]

    prepared = SandboxedCLITransport._prepare_command_for_runtime(
        command,
        use_jailed_paths=False,
    )

    assert prepared == command


def test_transport_rewrites_resolved_bundled_claude_path_for_jail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tracecat.agent.runtime.claude_code.transport.claude_agent_sdk.__file__",
        "/app/.venv/lib/python3.12/site-packages/claude_agent_sdk/__init__.py",
    )

    command = [
        "/app/.venv/bin/../lib/python3.12/site-packages/claude_agent_sdk/_bundled/claude",
        "--print",
    ]

    rewritten = SandboxedCLITransport._rewrite_command_for_jail(command)

    assert rewritten == [
        "/site-packages/claude_agent_sdk/_bundled/claude",
        "--print",
    ]


def test_transport_uses_fixed_mcp_bridge_port_for_jailed_runtime(
    tmp_path: Path,
) -> None:
    transport = _make_transport(tmp_path, use_jailed_paths=True)

    assert (
        transport._mcp_bridge_port_for_runtime()
        == transport_module.TRACECAT__AGENT_MCP_BRIDGE_PORT
    )


def test_transport_uses_dynamic_mcp_bridge_port_for_direct_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        SandboxedCLITransport,
        "_available_localhost_port",
        staticmethod(lambda: 54321),
    )
    transport = _make_transport(tmp_path, use_jailed_paths=False)

    assert transport._mcp_bridge_port_for_runtime() == 54321


def test_transport_rewrites_trusted_mcp_bridge_urls_for_selected_port(
    tmp_path: Path,
) -> None:
    transport = _make_transport(tmp_path, use_jailed_paths=False)
    mcp_servers: dict[str, McpServerConfig] = {
        "tracecat-registry": _trusted_mcp_config(4101, token="root"),
        "stdio-server": _stdio_mcp_config("tool"),
        "other-http-server": _trusted_mcp_config(4999, token="other"),
    }
    transport._options = ClaudeAgentOptions(
        mcp_servers=mcp_servers,
        agents={
            "analyst": AgentDefinition(
                description="Analyze scoped data",
                prompt="Analyze",
                mcpServers=[
                    {
                        "tracecat-registry-analyst": _trusted_mcp_config(
                            4101, token="child"
                        )
                    },
                    "stdio-server",
                ],
            )
        },
    )

    rewritten = transport._options_for_mcp_bridge_port(54321)

    mcp_servers = cast(dict[str, Any], rewritten.mcp_servers)
    registry_server = cast(dict[str, Any], mcp_servers["tracecat-registry"])
    assert registry_server["url"] == "http://127.0.0.1:54321/mcp"
    assert mcp_servers["stdio-server"] == {
        "type": "stdio",
        "command": "tool",
    }
    other_http_server = cast(dict[str, Any], mcp_servers["other-http-server"])
    assert other_http_server["url"] == "http://127.0.0.1:4999/mcp"
    assert rewritten.agents is not None
    agent = rewritten.agents["analyst"]
    assert agent.mcpServers is not None
    agent_mcp_entry = cast(dict[str, Any], agent.mcpServers[0])
    child_server = cast(
        dict[str, Any],
        agent_mcp_entry["tracecat-registry-analyst"],
    )
    assert child_server["url"] == "http://127.0.0.1:54321/mcp"
    assert agent.mcpServers[1] == "stdio-server"


def test_transport_mcp_bridge_url_rewrite_keeps_original_options(
    tmp_path: Path,
) -> None:
    transport = _make_transport(tmp_path, use_jailed_paths=False)
    original_options = ClaudeAgentOptions(
        mcp_servers={"tracecat-registry": _trusted_mcp_config(4101, token="root")}
    )
    transport._options = original_options

    rewritten = transport._options_for_mcp_bridge_port(54321)

    original_mcp_servers = cast(dict[str, Any], original_options.mcp_servers)
    rewritten_mcp_servers = cast(dict[str, Any], rewritten.mcp_servers)
    assert original_mcp_servers["tracecat-registry"]["url"] == (
        "http://127.0.0.1:4101/mcp"
    )
    assert rewritten_mcp_servers["tracecat-registry"]["url"] == (
        "http://127.0.0.1:54321/mcp"
    )
