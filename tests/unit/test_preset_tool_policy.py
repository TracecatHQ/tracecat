"""Policy is independent of whether a tool is authored or skill-derived."""

import uuid

import pytest

from tracecat.agent.preset.tool_policy import resolve_tool_policy
from tracecat.agent.preset.types import PresetToolInputs
from tracecat.db.models import (
    MCPIntegration,
    SkillVersion,
    SkillVersionMcpTool,
    SkillVersionTool,
)


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
    "whole_integration, expected", [(False, {}), (True, {"mcp.Synthetic.write": True})]
)
def test_mcp_approvals_apply_only_to_selected_available_tools(
    whole_integration: bool, expected: dict[str, bool]
) -> None:
    integration_id, version_id = uuid.uuid4(), uuid.uuid4()
    integration = MCPIntegration(
        id=integration_id,
        name="Synthetic",
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
