"""Unit tests for local MCP stdio sandbox transport config."""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest
from temporalio.workflow import _Definition as TemporalWorkflowDefinition

from tracecat.agent.common.exceptions import AgentSandboxValidationError
from tracecat.agent.mcp.sandbox import workflow as workflow_module
from tracecat.agent.worker import new_sandbox_runner


def _build_target(**overrides: object) -> SimpleNamespace:
    base = {
        "mcp_integration_id": uuid4(),
        "stdio_command": "npx",
        "stdio_args": ["@modelcontextprotocol/server-github"],
        "timeout": 30,
        "sandbox_allow_network": False,
        "sandbox_egress_allowlist": None,
        "sandbox_egress_denylist": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_build_stdio_client_config_warns_on_direct_fallback(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(workflow_module.config, "TRACECAT__DISABLE_NSJAIL", True)
    monkeypatch.setattr(workflow_module, "_DIRECT_SANDBOX_WARNING_EMITTED", False)
    monkeypatch.setattr(workflow_module, "_EGRESS_POLICY_DIRECT_WARNING_EMITTED", False)
    caplog.set_level(logging.WARNING)

    server_config, temp_dir = workflow_module._build_stdio_client_config(
        target=_build_target(),
        stdio_env={"PATH": "/usr/local/bin:/usr/bin:/bin"},
    )

    assert server_config["command"] == "npx"
    assert temp_dir is None
    assert workflow_module._DIRECT_SANDBOX_WARNING_EMITTED is True
    assert workflow_module._EGRESS_POLICY_DIRECT_WARNING_EMITTED is False


def test_build_stdio_client_config_warns_when_egress_policy_degrades_in_direct_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(workflow_module.config, "TRACECAT__DISABLE_NSJAIL", True)
    monkeypatch.setattr(workflow_module, "_DIRECT_SANDBOX_WARNING_EMITTED", False)
    monkeypatch.setattr(workflow_module, "_EGRESS_POLICY_DIRECT_WARNING_EMITTED", False)

    server_config, temp_dir = workflow_module._build_stdio_client_config(
        target=_build_target(sandbox_egress_allowlist=["api.github.com:443"]),
        stdio_env={"PATH": "/usr/local/bin:/usr/bin:/bin"},
    )

    assert server_config["command"] == "npx"
    assert temp_dir is None
    assert workflow_module._EGRESS_POLICY_DIRECT_WARNING_EMITTED is True


def test_build_stdio_client_config_wraps_with_nsjail(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    nsjail_path = tmp_path / "nsjail"
    nsjail_path.write_text("")
    rootfs_path = tmp_path / "rootfs"
    for subdir in ("usr", "lib", "bin", "etc"):
        (rootfs_path / subdir).mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(workflow_module.config, "TRACECAT__DISABLE_NSJAIL", False)
    monkeypatch.setattr(
        workflow_module.config, "TRACECAT__SANDBOX_NSJAIL_PATH", str(nsjail_path)
    )
    monkeypatch.setattr(
        workflow_module.config, "TRACECAT__SANDBOX_ROOTFS_PATH", str(rootfs_path)
    )
    monkeypatch.setattr(
        workflow_module,
        "_resolve_stdio_command_path",
        lambda command, env: "/usr/local/bin/npx",
    )

    stdio_env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(tmp_path / "cache" / "home"),
    }
    server_config, temp_dir = workflow_module._build_stdio_client_config(
        target=_build_target(
            sandbox_allow_network=True,
            sandbox_egress_allowlist=["api.github.com:443"],
        ),
        stdio_env=stdio_env,
    )

    assert server_config["command"] == str(nsjail_path)
    assert "--config" in server_config["args"]
    assert "TRACECAT__MCP_SANDBOX_EGRESS_ALLOWLIST" in server_config["env"]
    assert (
        server_config["env"]["LD_PRELOAD"]
        == "/usr/local/lib/libtracecat_mcp_egress_guard.so"
    )
    assert temp_dir is not None
    assert (temp_dir / "nsjail.cfg").exists()


def test_build_stdio_nsjail_config_rejects_unsafe_stdio_args(
    tmp_path: Path,
) -> None:
    command_path = tmp_path / "server"
    command_path.write_text("")
    cache_root = tmp_path / "cache"
    cache_root.mkdir()
    rootfs_path = tmp_path / "rootfs"
    for subdir in ("usr", "lib", "bin", "etc"):
        (rootfs_path / subdir).mkdir(parents=True, exist_ok=True)

    with pytest.raises(
        AgentSandboxValidationError, match="Invalid stdio arg at index 1"
    ):
        workflow_module._build_stdio_nsjail_config(
            command_path=str(command_path),
            command_args=["safe", 'unsafe" arg'],
            cache_root=cache_root,
            rootfs=rootfs_path,
            allow_network=False,
        )


@pytest.mark.anyio
async def test_local_mcp_workflow_prepares_in_temporal_sandbox() -> None:
    runner = new_sandbox_runner()
    workflow_definition = cast(
        TemporalWorkflowDefinition,
        getattr(
            workflow_module.RunLocalMCPArtifactWorkflow,
            "__temporal_workflow_definition",
        ),
    )
    runner.prepare_workflow(workflow_definition)
