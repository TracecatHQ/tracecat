"""Policy is independent of whether a tool is authored or skill-derived."""

import uuid
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config
from tracecat.agent.preset.schemas import AgentPresetToolPolicyPreview
from tracecat.agent.preset.service import AgentPresetService
from tracecat.agent.preset.tool_policy import resolve_tool_policy
from tracecat.agent.preset.types import PresetToolInputs
from tracecat.auth.types import Role
from tracecat.db.models import (
    AgentPresetVersion,
    MCPIntegration,
    SkillVersion,
    SkillVersionMcpTool,
    SkillVersionTool,
)
from tracecat.exceptions import TracecatValidationError
from tracecat.integrations.service import IntegrationService
from tracecat.registry.actions.service import RegistryActionsService


def test_namespace_policy_preserves_blocked_tool_provenance() -> None:
    skill_id, version_id = uuid.uuid4(), uuid.uuid4()
    version = SkillVersion(id=version_id, skill_id=skill_id, name="triage")
    version.tools = [SkillVersionTool(tool_id="tools.example.read")]
    version.mcp_tools = []
    policy = resolve_tool_policy(
        PresetToolInputs(
            uuid.uuid4(),
            ["core.cases.read", "tools.example.read"],
            ["core"],
            [],
            {},
            [version_id],
        ),
        {version_id: version},
        {},
    )
    assert policy.actions == ("core.cases.read",)
    assert [(source.tool_id, source.skill_id) for source in policy.blocked_tools] == [
        ("tools.example.read", None),
        ("tools.example.read", skill_id),
    ]


@pytest.mark.parametrize("direct", [False, True])
def test_stdio_requirement_is_independent_of_source(direct: bool) -> None:
    integration_id, version_id = uuid.uuid4(), uuid.uuid4()
    integration = MCPIntegration(
        id=integration_id, name="Synthetic", server_type="stdio"
    )
    version = SkillVersion(id=version_id, skill_id=uuid.uuid4(), name="triage")
    version.tools = []
    version.mcp_tools = [
        SkillVersionMcpTool(
            tool_id="mcp.synthetic", mcp_integration_id=integration_id, tool_name=None
        )
    ]
    policy = resolve_tool_policy(
        PresetToolInputs(
            uuid.uuid4(),
            [],
            [],
            [str(integration_id)] if direct else [],
            {},
            [] if direct else [version_id],
        ),
        {version_id: version},
        {integration_id: integration},
    )
    assert policy.requires_internet_access
    assert len(policy.internet_sources) == 1
    assert policy.mcp_grants[0].tool_names is None


@pytest.mark.parametrize(
    "whole_integration, expected",
    [
        (False, {}),
        (True, {"mcp.synthetic.write": True}),
    ],
)
def test_mcp_approvals_apply_only_to_selected_available_tools(
    whole_integration: bool, expected: dict[str, bool]
) -> None:
    integration_id, version_id = uuid.uuid4(), uuid.uuid4()
    integration = MCPIntegration(
        id=integration_id,
        name="Synthetic",
        slug="synthetic",
        server_type="http",
        tools=[
            {
                "name": "read",
                "enabled": True,
                "status": "available",
                "requires_approval": False,
            },
            {
                "name": "write",
                "enabled": True,
                "status": "available",
                "requires_approval": True,
            },
            {
                "name": "disabled",
                "enabled": False,
                "status": "available",
                "requires_approval": True,
            },
            {
                "name": "issue.get",
                "enabled": True,
                "status": "available",
                "requires_approval": True,
            },
            *[
                {"name": name, "requires_approval": True}
                for name in ("bad name", "x" * 65, "with\nnewline", "")
            ],
        ],
    )
    version = SkillVersion(id=version_id, skill_id=uuid.uuid4(), name="triage")
    version.tools = []
    version.mcp_tools = [
        SkillVersionMcpTool(
            tool_id="mcp.synthetic.read",
            mcp_integration_id=integration_id,
            tool_name="read",
        )
    ]
    policy = resolve_tool_policy(
        PresetToolInputs(
            uuid.uuid4(),
            [],
            [],
            [str(integration_id)] if whole_integration else [],
            {},
            [version_id],
        ),
        {version_id: version},
        {integration_id: integration},
    )
    assert policy.tool_approvals == expected
    assert not policy.requires_internet_access


@pytest.mark.anyio
async def test_runtime_loads_direct_mcp_metadata_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        config, "TRACECAT__DB_ENCRYPTION_KEY", Fernet.generate_key().decode()
    )
    workspace_id, integration_id = uuid.uuid4(), uuid.uuid4()
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=workspace_id,
        organization_id=uuid.uuid4(),
    )
    integration = MCPIntegration(
        id=integration_id,
        workspace_id=workspace_id,
        name="Synthetic",
        slug="synthetic",
        server_type="http",
        server_uri="https://mcp.example.test",
        tools=[],
    )
    version = AgentPresetVersion(
        id=uuid.uuid4(),
        preset_id=uuid.uuid4(),
        workspace_id=workspace_id,
        model_name="synthetic",
        model_provider="custom",
        instructions="",
        actions=[],
        namespaces=[],
        tool_approvals={},
        mcp_integrations=[str(integration_id)],
        agents={},
        retries=3,
        enable_thinking=False,
        enable_internet_access=False,
    )
    service = AgentPresetService(AsyncMock(spec=AsyncSession), role=role)
    monkeypatch.setattr(
        service.skills,
        "get_resolved_skill_refs_for_preset_version",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(service.skill_tools, "require_entitlement", AsyncMock())
    load = AsyncMock(return_value=[integration])
    monkeypatch.setattr(IntegrationService, "list_mcp_integrations", load)
    result = await service._version_to_agent_config(version)
    assert result.mcp_servers and result.mcp_servers[0].get("id") == str(integration_id)
    load.assert_awaited_once()


@pytest.mark.anyio
async def test_preview_loads_direct_mcp_metadata_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        config, "TRACECAT__DB_ENCRYPTION_KEY", Fernet.generate_key().decode()
    )
    integration_id = uuid.uuid4()
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )
    integration = MCPIntegration(
        id=integration_id,
        workspace_id=role.workspace_id,
        name="Synthetic",
        slug="synthetic",
        server_type="stdio",
        tools=[],
    )
    service = AgentPresetService(AsyncMock(spec=AsyncSession), role=role)
    monkeypatch.setattr(service.skill_tools, "require_entitlement", AsyncMock())
    load = AsyncMock(return_value=[integration])
    monkeypatch.setattr(IntegrationService, "list_mcp_integrations", load)
    monkeypatch.setattr(service.skills, "validate_binding_inputs", AsyncMock())
    monkeypatch.setattr(
        service, "_binding_specs_from_inputs", AsyncMock(return_value=[])
    )
    preview = await service.preview_tool_policy(
        AgentPresetToolPolicyPreview(mcp_integrations=[str(integration_id)])
    )
    assert preview.internet_sources[0].tool_id == f"mcp.{integration_id}"
    load.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize("selection", ["action", "malformed_mcp", "missing_mcp"])
async def test_preview_rejects_invalid_direct_selections(
    selection: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        config, "TRACECAT__DB_ENCRYPTION_KEY", Fernet.generate_key().decode()
    )
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )
    service = AgentPresetService(AsyncMock(spec=AsyncSession), role=role)
    monkeypatch.setattr(service.skills, "validate_binding_inputs", AsyncMock())
    monkeypatch.setattr(
        service, "_binding_specs_from_inputs", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        RegistryActionsService, "list_actions_from_index", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        IntegrationService, "list_mcp_integrations", AsyncMock(return_value=[])
    )
    params = AgentPresetToolPolicyPreview(
        actions=["tools.synthetic.missing"] if selection == "action" else [],
        mcp_integrations=(
            []
            if selection == "action"
            else ["not-a-uuid" if selection == "malformed_mcp" else str(uuid.uuid4())]
        ),
    )
    with pytest.raises(TracecatValidationError):
        await service.preview_tool_policy(params)
