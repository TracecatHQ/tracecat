from temporalio.converter import value_to_type

from tracecat.agent.types import AgentConfig


def test_temporal_converter_decodes_agent_config_with_mcp_servers() -> None:
    config = value_to_type(
        AgentConfig,
        {
            "model_name": "claude-3-5-sonnet-20241022",
            "model_provider": "anthropic",
            "mcp_servers": [
                {
                    "name": "Jira",
                    "type": "http",
                    "url": "https://mcp.atlassian.com/v1/mcp",
                    "headers": {"Authorization": "Bearer test-token"},
                }
            ],
        },
    )

    assert config.mcp_servers == [
        {
            "name": "Jira",
            "type": "http",
            "url": "https://mcp.atlassian.com/v1/mcp",
            "headers": {"Authorization": "Bearer test-token"},
        }
    ]
