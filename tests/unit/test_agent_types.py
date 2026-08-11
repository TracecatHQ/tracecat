import pytest
from temporalio.converter import value_to_type

from tracecat.agent import types as agent_types
from tracecat.agent.types import AgentConfig, resolve_agent_timeout_seconds
from tracecat.dsl._converter import _serializer


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


def test_resolve_agent_timeout_seconds_preserves_legacy_inheritance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(agent_types, "TRACECAT__AGENT_SANDBOX_TIMEOUT", 7200)

    assert resolve_agent_timeout_seconds(None) == 7200
    assert resolve_agent_timeout_seconds(900) == 900


def test_agent_config_omits_legacy_inherited_timeout_from_temporal_payload() -> None:
    config = AgentConfig(
        model_name="claude-sonnet-4-5",
        model_provider="anthropic",
    )

    serialized = _serializer(config)

    assert config.timeout_seconds is None
    assert isinstance(serialized, dict)
    assert "timeout_seconds" not in serialized
