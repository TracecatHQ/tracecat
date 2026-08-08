from __future__ import annotations

import pytest

from tracecat.agent.common.config import AGENT_RUNTIME_PROTECTED_ENV_VARS
from tracecat.integrations.mcp_validation import (
    MCPValidationError,
    validate_mcp_command_config,
    validate_mcp_env,
)


@pytest.mark.parametrize("env_key", sorted(AGENT_RUNTIME_PROTECTED_ENV_VARS))
def test_validate_mcp_env_rejects_agent_runtime_overrides(env_key: str) -> None:
    with pytest.raises(
        MCPValidationError,
        match=f"Cannot override protected env var: {env_key}",
    ):
        validate_mcp_env({env_key: "override"})


@pytest.mark.parametrize(
    "args",
    [
        pytest.param(
            ["--cache-dir", "/work/uv-cache", "example-mcp"],
            id="cache-dir-split",
        ),
        pytest.param(
            ["--cache-dir=/work/uv-cache", "example-mcp"],
            id="cache-dir-equals",
        ),
        pytest.param(
            ["--link-mode", "symlink", "example-mcp"],
            id="link-mode-split",
        ),
        pytest.param(
            ["--link-mode=symlink", "example-mcp"],
            id="link-mode-equals",
        ),
    ],
)
def test_validate_mcp_command_rejects_protected_uvx_options(
    args: list[str],
) -> None:
    with pytest.raises(
        MCPValidationError,
        match="Cannot override protected uvx option",
    ):
        validate_mcp_command_config(command="uvx", args=args)


def test_validate_mcp_command_allows_other_commands_to_use_same_option_names() -> None:
    validate_mcp_command_config(
        command="python",
        args=["server.py", "--cache-dir", "/work/server-cache"],
    )


def test_validate_mcp_command_preserves_uvx_tool_args_after_separator() -> None:
    validate_mcp_command_config(
        command="uvx",
        args=["example-mcp", "--", "--cache-dir", "/work/server-cache"],
    )
