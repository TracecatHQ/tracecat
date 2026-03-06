from __future__ import annotations

from pathlib import Path
from typing import Any

import mcp.types as mcp_types
import pytest

from tracecat.agent.common.types import MCPStdioServerConfig
from tracecat.agent.mcp.local_runtime import runtime
from tracecat.agent.mcp.local_runtime.types import (
    LocalMCPDiscoveryConfig,
    LocalMCPDiscoveryError,
    LocalMCPDiscoveryPhase,
)


def _config(tmp_path: Path, **kwargs: Any) -> LocalMCPDiscoveryConfig:
    allow_network = kwargs.pop("allow_network", True)
    server: MCPStdioServerConfig = {
        "type": "stdio",
        "name": "local-scope",
        "command": "uvx",
        "args": ["example-mcp"],
        "env": {"API_KEY": "secret"},
        "timeout": 30,
    }
    server.update(kwargs.pop("server", {}))
    return LocalMCPDiscoveryConfig(
        organization_id="org-123",
        sandbox_cache_dir=tmp_path,
        allow_network=allow_network,
        server=server,
        **kwargs,
    )


def _fake_client_factory(
    *,
    tools: list[mcp_types.Tool] | Exception,
    resources: list[mcp_types.Resource] | Exception,
    prompts: list[mcp_types.Prompt] | Exception,
    on_enter: Any = None,
):
    class FakeClient:
        last_transport: Any = None

        def __init__(self, transport: Any, timeout: int | None = None) -> None:
            self.transport = transport
            self.timeout = timeout
            FakeClient.last_transport = transport

        async def __aenter__(self) -> FakeClient:
            if callable(on_enter):
                on_enter(self.transport)
            return self

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            tb: Any,
        ) -> bool:
            return False

        async def list_tools(self) -> list[mcp_types.Tool]:
            if isinstance(tools, Exception):
                raise tools
            return tools

        async def list_resources(self) -> list[mcp_types.Resource]:
            if isinstance(resources, Exception):
                raise resources
            return resources

        async def list_prompts(self) -> list[mcp_types.Prompt]:
            if isinstance(prompts, Exception):
                raise prompts
            return prompts

    return FakeClient


@pytest.mark.anyio
async def test_discover_local_mcp_server_catalog_sets_cache_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    tool = mcp_types.Tool(
        name="search",
        description="Search docs",
        inputSchema={"type": "object"},
    )
    fake_client = _fake_client_factory(tools=[tool], resources=[], prompts=[])
    monkeypatch.setattr(runtime, "Client", fake_client)

    catalog = await runtime.discover_local_mcp_server_catalog(_config(tmp_path))

    assert catalog.server_name == "local-scope"
    transport = fake_client.last_transport
    assert transport is not None
    assert transport.env["API_KEY"] == "secret"
    assert transport.env["TRACECAT_MCP_ALLOW_NETWORK"] == "1"
    assert transport.env["UV_CACHE_DIR"].endswith("/org-123/uv-cache")
    assert transport.env["npm_config_cache"].endswith("/org-123/npm-cache")
    assert Path(transport.env["HOME"]).name == "home"
    assert Path(transport.env["UV_CACHE_DIR"]).is_dir()
    assert Path(transport.env["npm_config_cache"]).is_dir()


@pytest.mark.anyio
async def test_discover_local_mcp_server_catalog_wraps_transport_with_nsjail(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, str] = {}

    def _on_enter(transport: Any) -> None:
        config_index = transport.args.index("--config")
        config_path = Path(transport.args[config_index + 1])
        captured["config_text"] = config_path.read_text(encoding="utf-8")

    tool = mcp_types.Tool(
        name="search",
        description="Search docs",
        inputSchema={"type": "object"},
    )
    fake_client = _fake_client_factory(
        tools=[tool],
        resources=[],
        prompts=[],
        on_enter=_on_enter,
    )
    monkeypatch.setattr(runtime, "Client", fake_client)
    monkeypatch.setattr(runtime, "is_nsjail_available", lambda: True)
    monkeypatch.setattr(
        runtime,
        "_resolve_command_path",
        lambda _command: "/usr/local/bin/uvx",
    )

    rootfs = tmp_path / "rootfs"
    for dirname in ("usr", "lib", "bin", "etc"):
        (rootfs / dirname).mkdir(parents=True, exist_ok=True)
    nsjail_path = tmp_path / "bin" / "nsjail"
    nsjail_path.parent.mkdir(parents=True, exist_ok=True)
    nsjail_path.write_text("", encoding="utf-8")
    monkeypatch.setattr(runtime.config, "TRACECAT__SANDBOX_ROOTFS_PATH", str(rootfs))
    monkeypatch.setattr(
        runtime.config,
        "TRACECAT__SANDBOX_NSJAIL_PATH",
        str(nsjail_path),
    )

    catalog = await runtime.discover_local_mcp_server_catalog(
        _config(tmp_path, allow_network=False)
    )

    assert catalog.server_name == "local-scope"
    transport = fake_client.last_transport
    assert transport is not None
    assert transport.command == str(nsjail_path)
    assert transport.env["HOME"] == "/home/agent"
    assert transport.env["UV_CACHE_DIR"] == "/cache/uv"
    assert transport.env["TRACECAT_MCP_ALLOW_NETWORK"] == "0"

    config_text = captured["config_text"]
    assert "clone_newnet: true" in config_text
    assert 'exec_bin { path: "/usr/local/bin/uvx" arg: "example-mcp" }' in config_text
    assert str(runtime._JAILED_UV_CACHE_DIR) in config_text


@pytest.mark.anyio
async def test_discover_local_mcp_server_catalog_maps_list_tools_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        runtime,
        "Client",
        _fake_client_factory(
            tools=RuntimeError("boom"),
            resources=[],
            prompts=[],
        ),
    )

    with pytest.raises(LocalMCPDiscoveryError) as exc_info:
        await runtime.discover_local_mcp_server_catalog(_config(tmp_path))

    assert exc_info.value.phase == LocalMCPDiscoveryPhase.LIST_TOOLS


@pytest.mark.anyio
async def test_discover_local_mcp_server_catalog_requires_nsjail_for_network_isolation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(runtime, "is_nsjail_available", lambda: False)

    with pytest.raises(LocalMCPDiscoveryError) as exc_info:
        await runtime.discover_local_mcp_server_catalog(
            _config(tmp_path, allow_network=False)
        )

    assert exc_info.value.phase == LocalMCPDiscoveryPhase.CONFIG_VALIDATION
    assert "requires nsjail" in exc_info.value.summary


@pytest.mark.anyio
async def test_discover_local_mcp_server_catalog_applies_egress_policies(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, str] = {}

    def _on_enter(transport: Any) -> None:
        config_index = transport.args.index("--config")
        config_path = Path(transport.args[config_index + 1])
        captured["config_text"] = config_path.read_text(encoding="utf-8")
        captured["hosts_text"] = (config_path.parent / "hosts").read_text(
            encoding="utf-8"
        )
        captured["nsswitch_text"] = (config_path.parent / "nsswitch.conf").read_text(
            encoding="utf-8"
        )

    tool = mcp_types.Tool(
        name="search",
        description="Search docs",
        inputSchema={"type": "object"},
    )
    fake_client = _fake_client_factory(
        tools=[tool],
        resources=[],
        prompts=[],
        on_enter=_on_enter,
    )
    monkeypatch.setattr(runtime, "Client", fake_client)
    monkeypatch.setattr(runtime, "is_nsjail_available", lambda: True)
    monkeypatch.setattr(
        runtime,
        "_resolve_command_path",
        lambda _command: "/usr/local/bin/uvx",
    )
    monkeypatch.setattr(
        runtime.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (
                runtime.socket.AF_INET,
                runtime.socket.SOCK_STREAM,
                runtime.socket.IPPROTO_TCP,
                "",
                ("93.184.216.34", 0),
            )
        ],
    )

    rootfs = tmp_path / "rootfs"
    for dirname in ("usr", "lib", "bin", "etc"):
        (rootfs / dirname).mkdir(parents=True, exist_ok=True)
    nsjail_path = tmp_path / "bin" / "nsjail"
    nsjail_path.parent.mkdir(parents=True, exist_ok=True)
    nsjail_path.write_text("", encoding="utf-8")
    monkeypatch.setattr(runtime.config, "TRACECAT__SANDBOX_ROOTFS_PATH", str(rootfs))
    monkeypatch.setattr(
        runtime.config,
        "TRACECAT__SANDBOX_NSJAIL_PATH",
        str(nsjail_path),
    )

    catalog = await runtime.discover_local_mcp_server_catalog(
        _config(
            tmp_path,
            egress_allowlist=("api.example.com",),
            egress_denylist=("10.0.0.0/8",),
        )
    )

    assert catalog.server_name == "local-scope"
    config_text = captured["config_text"]
    assert 'path: "/usr/bin/env"' in config_text
    assert (
        'arg: "LD_PRELOAD=/usr/local/lib/libtracecat_mcp_egress_guard.so"'
        in config_text
    )
    assert 'arg: "TRACECAT_MCP_EGRESS_ALLOW_CIDRS=93.184.216.34/32"' in config_text
    assert 'arg: "TRACECAT_MCP_EGRESS_DENY_CIDRS=10.0.0.0/8"' in config_text
    assert 'dst: "/etc/hosts"' in config_text
    assert "93.184.216.34 api.example.com" in captured["hosts_text"]
    assert "hosts: files" in captured["nsswitch_text"]


@pytest.mark.anyio
async def test_discover_local_mcp_server_catalog_classifies_package_fetch_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def _on_enter(transport: Any) -> None:
        log_path = Path(transport.log_file)
        log_path.write_text("npm ERR! network fetch failed\n", encoding="utf-8")
        raise RuntimeError("package install failed")

    monkeypatch.setattr(
        runtime,
        "Client",
        _fake_client_factory(
            tools=[],
            resources=[],
            prompts=[],
            on_enter=_on_enter,
        ),
    )

    with pytest.raises(LocalMCPDiscoveryError) as exc_info:
        await runtime.discover_local_mcp_server_catalog(_config(tmp_path))

    assert exc_info.value.phase == LocalMCPDiscoveryPhase.PACKAGE_FETCH_INSTALL


def test_build_exec_bin_line_escapes_textproto_strings() -> None:
    arg = '--header=Bearer "token"\nnext-line'

    line = runtime._build_exec_bin_line(
        command_path="/usr/bin/node",
        args=[arg],
        policy=runtime._ResolvedEgressPolicy(),
    )

    assert line.startswith('exec_bin { path: "/usr/bin/node"')
    assert r'arg: "--header=Bearer \"token\"\nnext-line"' in line
    assert arg not in line
