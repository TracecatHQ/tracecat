"""Isolated runtime configuration; production options and hooks remain intact."""

import asyncio
import socket
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
import uvicorn
from claude_agent_sdk import ClaudeAgentOptions
from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport
from claude_agent_sdk.types import McpHttpServerConfig

from tests.agent_runtime_contracts.mcp_fixture import make_server
from tests.agent_runtime_contracts.model_fixture import ModelFixture
from tracecat.agent.common.protocol import RuntimeInitPayload
from tracecat.agent.common.types import (
    MCPToolDefinition,
    SandboxAgentConfig,
    SandboxSubagentConfig,
)
from tracecat.agent.runtime.claude_code.runtime import (
    ClaudeAgentRuntime,
    RuntimeEventWriter,
)


@asynccontextmanager
async def http_mcp(*, always_load: bool) -> AsyncIterator[str]:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            make_server(always_load=always_load).http_app(),
            log_level="error",
            lifespan="on",
        )
    )
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(10):
            while not server.started:
                if task.done():
                    await task
                    raise RuntimeError("MCP fixture did not start")
                await asyncio.sleep(0.01)
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        server.should_exit = True
        await task
        listener.close()


@asynccontextmanager
async def runtime_case(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    transport_kind: str = "http",
    provider: str = "anthropic",
    child: bool = False,
    eager_mode: str = "tool",
):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home / ".claude"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic-fixture-key")
    monkeypatch.setenv("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "1")
    monkeypatch.setenv("MCP_CONNECTION_NONBLOCKING", "0")
    for flag in (
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
        "CLAUDE_CODE_USE_FOUNDRY",
    ):
        monkeypatch.setenv(flag, "0")
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    writer = Mock(spec=RuntimeEventWriter)
    for method in (
        "send_stream_event",
        "send_session_line",
        "send_result",
        "send_error",
        "send_done",
        "send_log",
    ):
        setattr(writer, method, AsyncMock())
    model = ModelFixture(
        child=child, transport_kind=transport_kind, discovery=eager_mode == "none"
    )
    async with (
        http_mcp(always_load=eager_mode == "tool") as mcp_url,
        model.serve() as model_url,
    ):
        monkeypatch.setenv("ANTHROPIC_BASE_URL", model_url)

        def transport(options: ClaudeAgentOptions) -> SubprocessCLITransport:
            # Substitute only external endpoints/process isolation. Runtime option
            # construction, hooks, streaming, and the actual SDK/CLI remain real.
            if transport_kind == "http":
                endpoint: McpHttpServerConfig = {"type": "http", "url": mcp_url}
                options.mcp_servers = {"tracecat-registry": endpoint}
                if child:
                    assert options.agents is not None
                    definition = options.agents["fixture-child"]
                    definition.mcpServers = [
                        {
                            ClaudeAgentRuntime._subagent_registry_server_name(
                                "fixture-child"
                            ): endpoint
                        }
                    ]
            if eager_mode == "server":
                assert isinstance(options.mcp_servers, dict)
                for config in options.mcp_servers.values():
                    # CLI extension not yet represented in the SDK TypedDict.
                    cast(dict[str, object], config)["alwaysLoad"] = True
            options.sandbox = {"enabled": False}
            options.max_turns = 3
            options.extra_args = {"debug-file": str(tmp_path / "cli-debug.log")}
            return SubprocessCLITransport(prompt="", options=options)

        runtime = ClaudeAgentRuntime(
            writer,
            transport_factory=transport,
            session_home_dir=home,
            cwd=tmp_path,
            cwd_setup_path=tmp_path,
        )
        payload = RuntimeInitPayload(
            session_id=uuid4(),
            mcp_auth_token="synthetic-mcp-token",
            llm_gateway_auth_token="synthetic-model-token",
            user_prompt="Use required_lookup for device-123.",
            config=SandboxAgentConfig(
                model_name="claude-sonnet-4-6",
                model_provider=provider,
                instructions="Use the required tool.",
                enable_thinking=False,
            ),
            allowed_actions={
                "required_lookup": MCPToolDefinition(
                    name="required_lookup",
                    description="Required",
                    parameters_json_schema={
                        "type": "object",
                        "properties": {"device_id": {"type": "string"}},
                    },
                ),
                "unrelated_lookup": MCPToolDefinition(
                    name="unrelated_lookup",
                    description="Unrelated",
                    parameters_json_schema={
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                    },
                ),
            },
        )
        if transport_kind == "stdio":
            payload.allowed_actions = {}
            payload.config.mcp_servers = [
                {
                    "type": "stdio",
                    "name": "fixture-tools",
                    "command": sys.executable,
                    "args": [
                        str(Path(__file__).with_name("mcp_fixture.py")),
                        *([] if eager_mode == "tool" else ["--deferred"]),
                    ],
                    "tools": [
                        {"name": "required_lookup"},
                        {"name": "unrelated_lookup"},
                    ],
                }
            ]
        if child:
            payload.subagents = [
                SandboxSubagentConfig(
                    alias="fixture-child",
                    description="Synthetic readiness child",
                    prompt="CHILD_READINESS_SENTINEL. Use required_lookup.",
                    config=payload.config,
                    mcp_auth_token="synthetic-child-token",
                    allowed_actions=payload.allowed_actions,
                    max_turns=3,
                )
            ]
        yield runtime, payload, writer, model
