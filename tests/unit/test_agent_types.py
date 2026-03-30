from temporalio.converter import value_to_type

from tracecat.agent.types import (
    AgentConfig,
    CustomModelSourceFlavor,
    parse_custom_source_flavor,
)


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


def test_parse_custom_source_flavor_normalizes_legacy_litellm_value() -> None:
    assert (
        parse_custom_source_flavor("litellm")
        is CustomModelSourceFlavor.GENERIC_OPENAI_COMPATIBLE
    )
