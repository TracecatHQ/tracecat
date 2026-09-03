from __future__ import annotations

from typing import cast

import pytest

from tracecat.agent.internal_router import _resolve_run_config
from tracecat.agent.schemas import AgentConfigSchema, InternalRunAgentRequest
from tracecat.agent.service import AgentManagementService
from tracecat.agent.subagents import AgentSubagentsConfig


@pytest.mark.anyio
async def test_resolve_run_config_rejects_subagents_on_internal_runner() -> None:
    request = InternalRunAgentRequest(
        user_prompt="Investigate this alert",
        config=AgentConfigSchema(
            model_name="gpt-5",
            model_provider="openai",
            agents=AgentSubagentsConfig(enabled=True),
        ),
    )

    with pytest.raises(ValueError, match="Subagents are not supported"):
        await _resolve_run_config(request, cast(AgentManagementService, object()))
