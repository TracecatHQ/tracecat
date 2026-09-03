import pytest
from temporalio.converter import value_to_type

from tracecat.agent import types as agent_types
from tracecat.agent.types import AgentConfig, clamp_agent_timeout_seconds


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


def test_clamp_agent_timeout_seconds_clamps_to_ceiling() -> None:
    # Hardcoded default floor 1800; default ceiling 3600.
    assert clamp_agent_timeout_seconds(None) == 1800
    assert clamp_agent_timeout_seconds(900) == 1800
    assert clamp_agent_timeout_seconds(2000) == 2000
    assert clamp_agent_timeout_seconds(100_000) == 3600


def test_clamp_agent_timeout_seconds_honors_raised_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(agent_types, "TRACECAT__AGENT_SANDBOX_TIMEOUT", 7200)

    assert clamp_agent_timeout_seconds(None) == 1800
    assert clamp_agent_timeout_seconds(5000) == 5000
    assert clamp_agent_timeout_seconds(100_000) == 7200


def test_clamp_agent_timeout_seconds_lowered_ceiling_wins_over_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(agent_types, "TRACECAT__AGENT_SANDBOX_TIMEOUT", 900)

    assert clamp_agent_timeout_seconds(None) == 900
    assert clamp_agent_timeout_seconds(60) == 900
    assert clamp_agent_timeout_seconds(2000) == 900
