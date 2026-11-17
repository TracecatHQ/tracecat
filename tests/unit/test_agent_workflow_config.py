from tracecat.agent.types import AgentConfig
from tracecat.agent.workflow_config import (
    agent_config_from_payload,
    agent_config_to_payload,
)


def test_http_mcp_tool_subset_round_trips_through_workflow_payload() -> None:
    config = AgentConfig(
        model_name="test-model",
        model_provider="test-provider",
        mcp_servers=[
            {
                "type": "http",
                "name": "issue-tracker",
                "url": "https://mcp.example.com",
                "id": "11111111-1111-1111-1111-111111111111",
                "tools": [
                    {
                        "name": "get_issue",
                        "description": "Get an issue",
                        "requires_approval": True,
                    }
                ],
            }
        ],
    )

    restored = agent_config_from_payload(agent_config_to_payload(config))

    assert restored.mcp_servers is not None
    assert restored.mcp_servers[0].get("tools") == [
        {
            "name": "get_issue",
            "description": "Get an issue",
            "enabled": True,
            "requires_approval": True,
            "status": "available",
        }
    ]


def test_http_mcp_tunnel_round_trips_through_workflow_payload() -> None:
    tunnel_id = "22222222-2222-2222-2222-222222222222"
    config = AgentConfig(
        model_name="test-model",
        model_provider="test-provider",
        mcp_servers=[
            {
                "type": "http",
                "name": "private",
                "url": "https://mcp.internal.example",
                "id": "11111111-1111-1111-1111-111111111111",
                "tunnel_id": tunnel_id,
            }
        ],
    )

    restored = agent_config_from_payload(agent_config_to_payload(config))

    assert restored.mcp_servers is not None
    assert restored.mcp_servers[0].get("tunnel_id") == tunnel_id
