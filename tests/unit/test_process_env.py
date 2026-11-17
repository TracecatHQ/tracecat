"""Loader variables never reach a sandbox launcher's process environment.

nsjail runs on the host with the env map it is given, before any jail exists,
so a loader variable set by a workflow author would steer nsjail's own dynamic
loader. One shared predicate decides these keys for every launcher.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

import tracecat.agent.runtime.claude_code.runtime as runtime_module
import tracecat.sandbox.executor as executor_module
from tracecat.agent.common.exceptions import AgentSandboxValidationError
from tracecat.agent.common.types import MCPServerConfig
from tracecat.agent.runtime.claude_code.runtime import ClaudeAgentRuntime
from tracecat.agent.sandbox.config import AgentSandboxConfig, build_agent_env_map
from tracecat.integrations.mcp_validation import MCPValidationError, validate_mcp_env
from tracecat.process_env import LOADER_ENV_VAR_ERROR, is_loader_env_var
from tracecat.sandbox.exceptions import SandboxValidationError
from tracecat.sandbox.executor import ActionSandboxConfig, NsjailExecutor
from tracecat.sandbox.types import SandboxConfig

LOADER_KEYS = [
    "LD_PRELOAD",
    "LD_LIBRARY_PATH",
    "LD_AUDIT",
    "LD_DEBUG",
    "GLIBC_TUNABLES",
    "GCONV_PATH",
]
ORDINARY_KEYS = ["API_TOKEN", "LDAP_URL", "MY_LD_PRELOAD"]
# A loader-prefixed key that could be a resolved secret; it must never be echoed.
SECRET_SHAPED_KEY = "LD_canary_7f3a91"
LOADER_ERROR_PATTERN = re.escape(LOADER_ENV_VAR_ERROR)


class _NsjailLaunched(Exception):
    """Raised by the fake launcher once it has recorded the nsjail env."""


@pytest.fixture
def nsjail_envs(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, str]]:
    """Record the env each nsjail launch would receive, without launching."""
    envs: list[dict[str, str]] = []

    async def fake_invoke_nsjail(**kwargs: Any) -> None:
        envs.append(dict(kwargs["env"]))
        raise _NsjailLaunched

    monkeypatch.setattr(executor_module, "invoke_nsjail", fake_invoke_nsjail)
    return envs


def _action_config(tmp_path: Path, env_vars: dict[str, str]) -> ActionSandboxConfig:
    return ActionSandboxConfig(
        registry_paths=[tmp_path / "registry"],
        tracecat_app_dir=tmp_path / "app",
        env_vars=env_vars,
    )


@pytest.mark.parametrize("key", LOADER_KEYS)
def test_loader_keys_are_recognized(key: str) -> None:
    assert is_loader_env_var(key)


@pytest.mark.parametrize("key", ORDINARY_KEYS)
def test_ordinary_keys_are_not_loader_keys(key: str) -> None:
    assert not is_loader_env_var(key)


@pytest.mark.anyio
@pytest.mark.parametrize("key", LOADER_KEYS)
async def test_python_script_env_refuses_loader_keys_before_launch(
    tmp_path: Path, nsjail_envs: list[dict[str, str]], key: str
) -> None:
    """run_python env_vars must fail closed before nsjail starts."""
    executor = NsjailExecutor(rootfs_path=str(tmp_path / "rootfs"))
    config = SandboxConfig(env_vars={key: "/nonexistent.so"})

    with pytest.raises(SandboxValidationError, match=LOADER_ERROR_PATTERN):
        await executor.execute(tmp_path, config)

    assert nsjail_envs == []


@pytest.mark.anyio
async def test_python_script_env_passes_ordinary_keys(
    tmp_path: Path, nsjail_envs: list[dict[str, str]]
) -> None:
    executor = NsjailExecutor(rootfs_path=str(tmp_path / "rootfs"))
    env_vars = dict.fromkeys(ORDINARY_KEYS, "value")

    with pytest.raises(_NsjailLaunched):
        await executor.execute(tmp_path, SandboxConfig(env_vars=env_vars))

    [env] = nsjail_envs
    assert env.items() >= env_vars.items()
    assert not any(is_loader_env_var(key) for key in env)


@pytest.mark.anyio
@pytest.mark.parametrize("key", LOADER_KEYS)
async def test_action_env_refuses_loader_keys_before_launch(
    tmp_path: Path, nsjail_envs: list[dict[str, str]], key: str
) -> None:
    executor = NsjailExecutor(rootfs_path=str(tmp_path / "rootfs"))

    with pytest.raises(SandboxValidationError, match=LOADER_ERROR_PATTERN):
        await executor.execute_action(
            tmp_path, _action_config(tmp_path, {key: "/nonexistent.so"})
        )

    assert nsjail_envs == []


@pytest.mark.anyio
async def test_action_env_passes_ordinary_keys(
    tmp_path: Path, nsjail_envs: list[dict[str, str]]
) -> None:
    executor = NsjailExecutor(rootfs_path=str(tmp_path / "rootfs"))
    env_vars = dict.fromkeys(ORDINARY_KEYS, "value")

    with pytest.raises(_NsjailLaunched):
        await executor.execute_action(tmp_path, _action_config(tmp_path, env_vars))

    [env] = nsjail_envs
    assert env.items() >= env_vars.items()


@pytest.mark.parametrize("key", LOADER_KEYS)
def test_agent_env_map_refuses_loader_keys(key: str) -> None:
    with pytest.raises(AgentSandboxValidationError, match=LOADER_ERROR_PATTERN):
        build_agent_env_map(AgentSandboxConfig(env_vars={key: "/nonexistent.so"}))


def test_agent_env_map_passes_ordinary_keys() -> None:
    env_vars = dict.fromkeys(ORDINARY_KEYS, "value")

    env = build_agent_env_map(AgentSandboxConfig(env_vars=env_vars))

    assert env.items() >= env_vars.items()


@pytest.mark.parametrize("key", LOADER_KEYS)
def test_mcp_validation_refuses_loader_keys(key: str) -> None:
    with pytest.raises(MCPValidationError, match=LOADER_ERROR_PATTERN):
        validate_mcp_env({key: "/nonexistent.so"})


def test_mcp_validation_passes_ordinary_keys() -> None:
    validate_mcp_env(dict.fromkeys(ORDINARY_KEYS, "value"))


def test_agent_runtime_drops_loader_keys_from_stdio_servers() -> None:
    """Configs persisted before validation refused these keys are still cleaned."""
    ordinary = dict.fromkeys(ORDINARY_KEYS, "value")
    configs: list[MCPServerConfig] = [
        {
            "type": "stdio",
            "name": "example",
            "command": "npx",
            "env": {**ordinary, **dict.fromkeys(LOADER_KEYS, "/nonexistent.so")},
        }
    ]

    spec = ClaudeAgentRuntime._stdio_mcp_server_spec(source_configs=configs)

    assert spec.servers["example"].get("env") == ordinary


@pytest.mark.anyio
async def test_refusals_do_not_echo_loader_prefixed_keys(tmp_path: Path) -> None:
    """A prefix match is not a known constant, so errors must not name the key."""
    executor = NsjailExecutor(rootfs_path=str(tmp_path / "rootfs"))
    env_vars = {SECRET_SHAPED_KEY: "value"}
    errors: list[Exception] = []

    for refuse in (
        lambda: executor._build_env_map(SandboxConfig(env_vars=env_vars), "execute"),
        lambda: executor._build_action_env_map(_action_config(tmp_path, env_vars)),
        lambda: build_agent_env_map(AgentSandboxConfig(env_vars=env_vars)),
        lambda: validate_mcp_env(env_vars),
    ):
        with pytest.raises(Exception) as exc_info:
            refuse()
        errors.append(exc_info.value)

    for error in errors:
        assert LOADER_ENV_VAR_ERROR in str(error)
        assert SECRET_SHAPED_KEY not in str(error)


def test_agent_runtime_counts_loader_keys_without_logging_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = MagicMock()
    monkeypatch.setattr(runtime_module, "logger", logger)
    configs: list[MCPServerConfig] = [
        {
            "type": "stdio",
            "name": "example",
            "command": "npx",
            "env": {SECRET_SHAPED_KEY: "value", "UV_LINK_MODE": "symlink"},
        }
    ]

    ClaudeAgentRuntime._stdio_mcp_server_spec(source_configs=configs)

    logger.warning.assert_called_once()
    kwargs = logger.warning.call_args.kwargs
    assert kwargs["env_keys"] == ["UV_LINK_MODE"]
    assert kwargs["loader_env_key_count"] == 1
    assert SECRET_SHAPED_KEY not in repr(logger.warning.call_args)
