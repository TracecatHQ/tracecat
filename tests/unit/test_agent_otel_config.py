import pytest
from pydantic import ValidationError

from tracecat.agent.otel_config import (
    AgentOtelConfig,
    AgentOtelPlatformOverride,
    load_agent_otel_platform_override,
    resolve_agent_otel_config,
)


def test_resolve_org_agent_otel_config_redirects_sandbox_to_relay() -> None:
    resolved = resolve_agent_otel_config(
        org_config=AgentOtelConfig(
            enabled=True,
            env={
                "OTEL_LOGS_EXPORTER": "otlp",
                "OTEL_EXPORTER_OTLP_ENDPOINT": "https://collector.example.com",
            },
        ),
        org_headers={"Authorization": "Bearer token"},
        platform_override=None,
        relay_endpoint="http://127.0.0.1:4318",
        relay_timeout_seconds=3.5,
    )

    assert resolved.enabled is True
    assert resolved.source == "org"
    assert resolved.sandbox_env["CLAUDE_CODE_ENABLE_TELEMETRY"] == "1"
    assert (
        resolved.sandbox_env["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://127.0.0.1:4318"
    )
    assert resolved.sandbox_env["OTEL_EXPORTER_OTLP_PROTOCOL"] == "http/protobuf"
    assert (
        resolved.collector_env["OTEL_EXPORTER_OTLP_ENDPOINT"]
        == "https://collector.example.com"
    )
    assert resolved.headers["Authorization"].get_secret_value() == "Bearer token"
    assert resolved.relay_timeout_seconds == 3.5


def test_platform_override_false_wins_over_enabled_org_config() -> None:
    resolved = resolve_agent_otel_config(
        org_config=AgentOtelConfig(
            enabled=True,
            env={
                "OTEL_LOGS_EXPORTER": "otlp",
                "OTEL_EXPORTER_OTLP_ENDPOINT": "https://org.example.com",
            },
        ),
        org_headers={"Authorization": "Bearer org"},
        platform_override=AgentOtelPlatformOverride(enabled=False),
    )

    assert resolved.enabled is False
    assert resolved.source == "platform"
    assert resolved.sandbox_env == {}
    assert resolved.collector_env == {}
    assert resolved.headers == {}


def test_agent_otel_config_rejects_headers_in_env_map() -> None:
    with pytest.raises(ValidationError, match="modeled separately"):
        AgentOtelConfig(
            enabled=True,
            env={"OTEL_EXPORTER_OTLP_HEADERS": "Authorization=Bearer token"},
        )


def test_agent_otel_config_rejects_unknown_env_var() -> None:
    with pytest.raises(ValidationError, match="Unsupported Claude Code OTel"):
        AgentOtelConfig(enabled=True, env={"OTEL_SOMETHING_ELSE": "1"})


def test_resolve_requires_endpoint_for_otlp_exporter() -> None:
    with pytest.raises(ValueError, match="requires OTEL_EXPORTER_OTLP_ENDPOINT"):
        resolve_agent_otel_config(
            org_config=AgentOtelConfig(
                enabled=True, env={"OTEL_LOGS_EXPORTER": "otlp"}
            ),
            org_headers=None,
            platform_override=None,
        )


def test_load_platform_override_parses_definitive_enabled_and_json() -> None:
    override = load_agent_otel_platform_override(
        enabled="false",
        env='{"OTEL_LOGS_EXPORTER":"console"}',
        headers='{"x-api-key":"secret"}',
    )

    assert override is not None
    assert override.enabled is False
    assert override.env == {"OTEL_LOGS_EXPORTER": "console"}
    assert override.headers["x-api-key"].get_secret_value() == "secret"


def test_load_platform_override_returns_none_when_enabled_unset() -> None:
    assert load_agent_otel_platform_override(enabled=None) is None
