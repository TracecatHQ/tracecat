import pytest
from temporalio.converter import value_to_type

from tracecat.agent import types as agent_types
from tracecat.agent.types import AgentConfig, resolve_agent_timeout_seconds


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


def test_resolve_agent_timeout_seconds_clamps_to_deployment_bounds() -> None:
    # Defaults: floor 1800 (deployment default), cap 3600.
    assert resolve_agent_timeout_seconds(None) == 1800
    assert resolve_agent_timeout_seconds(900) == 1800
    assert resolve_agent_timeout_seconds(2000) == 2000
    assert resolve_agent_timeout_seconds(100_000) == 3600


def test_resolve_agent_timeout_seconds_caps_the_deployment_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(agent_types, "TRACECAT__AGENT_SANDBOX_TIMEOUT", 7200)

    assert resolve_agent_timeout_seconds(None) == 3600
    assert resolve_agent_timeout_seconds(900) == 3600


def test_resolve_agent_timeout_seconds_honors_raised_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(agent_types, "TRACECAT__AGENT_TIMEOUT_MAX", 7200)
    monkeypatch.setattr(agent_types, "TRACECAT__AGENT_SANDBOX_TIMEOUT", 7200)

    assert resolve_agent_timeout_seconds(None) == 7200
    assert resolve_agent_timeout_seconds(100_000) == 7200
