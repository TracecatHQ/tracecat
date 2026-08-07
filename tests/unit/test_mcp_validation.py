from __future__ import annotations

import pytest

from tracecat.agent.common.config import AGENT_RUNTIME_PROTECTED_ENV_VARS
from tracecat.integrations.mcp_validation import MCPValidationError, validate_mcp_env


@pytest.mark.parametrize("env_key", sorted(AGENT_RUNTIME_PROTECTED_ENV_VARS))
def test_validate_mcp_env_rejects_agent_runtime_overrides(env_key: str) -> None:
    with pytest.raises(
        MCPValidationError,
        match=f"Cannot override protected env var: {env_key}",
    ):
        validate_mcp_env({env_key: "override"})
