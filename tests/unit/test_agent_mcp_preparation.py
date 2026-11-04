from unittest.mock import Mock

import pytest
from temporalio import workflow
from tracecat_ee.agent.activities import (
    BuildAgentToolDefsArgs,
    BuildAgentToolDefsResult,
    BuildToolDefsArgs,
    BuildToolDefsResult,
)
from tracecat_ee.agent.workflows import durable

from tracecat.agent.types import AgentConfig
from tracecat.auth.types import Role
from tracecat.registry.lock.types import RegistryLock
from tracecat.temporal.patches import DurableAgentWorkflowPatch


@pytest.mark.anyio
@pytest.mark.parametrize("fail_on_root_error", [False, True])
@pytest.mark.parametrize("build_agent_scopes", [False, True])
async def test_root_discovery_policy_preserves_old_workflow_history(
    monkeypatch: pytest.MonkeyPatch,
    fail_on_root_error: bool,
    build_agent_scopes: bool,
) -> None:
    """Verify activity inputs on both sides of the durable workflow patch."""

    def patched(marker: str) -> bool:
        markers: dict[str, bool] = {
            DurableAgentWorkflowPatch.FAIL_ON_ROOT_MCP_DISCOVERY_ERROR: fail_on_root_error,
            DurableAgentWorkflowPatch.BUILD_AGENT_TOOL_DEFINITIONS: build_agent_scopes,
        }
        return markers[marker]

    result = BuildToolDefsResult(
        tool_definitions={}, registry_lock=RegistryLock(origins={}, actions={})
    )
    captured: list[BuildToolDefsArgs | BuildAgentToolDefsArgs] = []

    async def execute_activity(
        method: object,
        *,
        arg: BuildToolDefsArgs | BuildAgentToolDefsArgs,
        **kwargs: object,
    ) -> BuildToolDefsResult | BuildAgentToolDefsResult:
        captured.append(arg)
        if isinstance(arg, BuildAgentToolDefsArgs):
            return BuildAgentToolDefsResult(scopes={"root": result})
        return result

    monkeypatch.setattr(workflow, "patched", patched)
    monkeypatch.setattr(workflow, "execute_activity_method", execute_activity)
    instance = object.__new__(durable.DurableAgentWorkflow)
    instance.role = Role(type="service", service_id="tracecat-service")
    monkeypatch.setattr(instance, "_mint_scope_mcp_token", Mock(return_value="token"))
    compiled = await instance._compile_agent_run(
        cfg=AgentConfig(model_name="synthetic-model", model_provider="openai"),
        subagents=[],
        internal_tool_context=None,
        token_ttl_seconds=None,
    )
    assert compiled.root.spec.fail_on_mcp_discovery_error is fail_on_root_error
    assert len(captured) == 1
    args = captured[0]
    if isinstance(args, BuildAgentToolDefsArgs):
        assert len(args.scopes) == 1
        assert args.scopes[0].fail_on_mcp_discovery_error is fail_on_root_error
    else:
        assert args.fail_on_mcp_discovery_error is fail_on_root_error
