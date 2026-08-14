import pytest
from pydantic import AnyHttpUrl, SecretStr, ValidationError

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
            endpoint=AnyHttpUrl("https://collector.example.com"),
        ),
        org_headers={"Authorization": "Bearer token"},
        platform_override=None,
        relay_endpoint="http://127.0.0.1:4318",
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
        == "https://collector.example.com/"
    )
    assert resolved.headers["Authorization"].get_secret_value() == "Bearer token"


def test_resolve_org_agent_otel_config_without_relay_keeps_endpoint() -> None:
    resolved = resolve_agent_otel_config(
        org_config=AgentOtelConfig(
            enabled=True,
            protocol="grpc",
            endpoint=AnyHttpUrl("https://collector.example.com"),
        ),
        org_headers=None,
        platform_override=None,
    )

    assert resolved.sandbox_env["OTEL_EXPORTER_OTLP_PROTOCOL"] == "grpc"
    assert (
        resolved.sandbox_env["OTEL_EXPORTER_OTLP_ENDPOINT"]
        == "https://collector.example.com/"
    )
    assert resolved.headers == {}


def test_platform_override_wins_over_enabled_org_config() -> None:
    resolved = resolve_agent_otel_config(
        org_config=AgentOtelConfig(
            enabled=True,
            endpoint=AnyHttpUrl("https://org.example.com"),
        ),
        org_headers={"Authorization": "Bearer org"},
        platform_override=AgentOtelPlatformOverride(
            config=AgentOtelConfig(
                enabled=True,
                endpoint=AnyHttpUrl("https://platform.example.com"),
            ),
            headers={"x-api-key": SecretStr("platform")},
        ),
    )

    assert resolved.source == "platform"
    assert (
        resolved.collector_env["OTEL_EXPORTER_OTLP_ENDPOINT"]
        == "https://platform.example.com/"
    )
    assert set(resolved.headers) == {"x-api-key"}


def test_platform_override_false_wins_over_enabled_org_config() -> None:
    resolved = resolve_agent_otel_config(
        org_config=AgentOtelConfig(
            enabled=True,
            endpoint=AnyHttpUrl("https://org.example.com"),
        ),
        org_headers={"Authorization": "Bearer org"},
        platform_override=AgentOtelPlatformOverride(
            config=AgentOtelConfig(enabled=False), headers={}
        ),
    )

    assert resolved.enabled is False
    assert resolved.source == "platform"
    assert resolved.sandbox_env == {}
    assert resolved.collector_env == {}
    assert resolved.headers == {}


def test_disabled_org_config_resolves_to_empty_envs() -> None:
    resolved = resolve_agent_otel_config(
        org_config=None,
        org_headers={"Authorization": "Bearer org"},
        platform_override=None,
    )

    assert resolved.enabled is False
    assert resolved.source == "org"
    assert resolved.sandbox_env == {}
    assert resolved.collector_env == {}


def test_agent_otel_config_rejects_raw_env_map() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        AgentOtelConfig.model_validate(
            {"enabled": True, "env": {"OTEL_LOGS_EXPORTER": "console"}}
        )


def test_agent_otel_config_rejects_exporter_lists() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        AgentOtelConfig.model_validate({"logs_exporters": ["otlp"]})


def test_agent_otel_config_rejects_non_positive_interval() -> None:
    with pytest.raises(ValidationError, match="greater than 0"):
        AgentOtelConfig(metric_export_interval_ms=0)


def test_agent_otel_config_rejects_empty_resource_attribute() -> None:
    with pytest.raises(ValidationError, match="cannot be empty"):
        AgentOtelConfig(resource_attributes={"service.name": "  "})


def test_agent_otel_config_requires_endpoint_when_enabled() -> None:
    with pytest.raises(ValidationError, match="no endpoint is configured"):
        AgentOtelConfig(enabled=True)


def test_agent_otel_config_requires_endpoint_for_single_signal() -> None:
    with pytest.raises(ValidationError, match="no endpoint is configured"):
        AgentOtelConfig(enabled=True, metrics_enabled=False)


def test_disabled_agent_otel_config_does_not_require_endpoint() -> None:
    otel_config = AgentOtelConfig(enabled=False)

    assert otel_config.enabled is False


def test_all_signals_off_does_not_require_endpoint() -> None:
    otel_config = AgentOtelConfig(
        enabled=True, metrics_enabled=False, logs_enabled=False
    )

    assert otel_config.to_env()["OTEL_METRICS_EXPORTER"] == "none"
    assert otel_config.to_env()["OTEL_LOGS_EXPORTER"] == "none"


def test_traces_alone_require_endpoint() -> None:
    with pytest.raises(ValidationError, match="no endpoint is configured"):
        AgentOtelConfig(
            enabled=True,
            metrics_enabled=False,
            logs_enabled=False,
            traces_enabled=True,
        )


def test_enabling_traces_sets_beta_flag() -> None:
    env = AgentOtelConfig(
        enabled=True,
        endpoint=AnyHttpUrl("https://collector.example.com"),
        traces_enabled=True,
    ).to_env()

    assert env["OTEL_TRACES_EXPORTER"] == "otlp"
    assert env["CLAUDE_CODE_ENHANCED_TELEMETRY_BETA"] == "1"


def test_disabled_traces_do_not_set_beta_flag() -> None:
    env = AgentOtelConfig(
        enabled=True, endpoint=AnyHttpUrl("https://collector.example.com")
    ).to_env()

    assert env["OTEL_TRACES_EXPORTER"] == "none"
    assert "CLAUDE_CODE_ENHANCED_TELEMETRY_BETA" not in env


def test_agent_otel_config_serializes_typed_fields_to_env() -> None:
    otel_config = AgentOtelConfig(
        enabled=True,
        protocol="http/protobuf",
        endpoint=AnyHttpUrl("https://collector.example.com:4318"),
        logs_enabled=False,
        metrics_temporality="cumulative",
        metric_export_interval_ms=10_000,
        logs_export_interval_ms=5_000,
        metrics_include_session_id=False,
        metrics_include_version=True,
        metrics_include_account_uuid=False,
        log_user_prompts=True,
        log_tool_details=False,
        log_tool_content=True,
        resource_attributes={"service.name": "tracecat agent", "key,1": "value=1"},
    )

    assert otel_config.to_env() == {
        "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
        "OTEL_EXPORTER_OTLP_ENDPOINT": "https://collector.example.com:4318/",
        "OTEL_METRICS_EXPORTER": "otlp",
        "OTEL_LOGS_EXPORTER": "none",
        "OTEL_TRACES_EXPORTER": "none",
        "OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE": "cumulative",
        "OTEL_METRIC_EXPORT_INTERVAL": "10000",
        "OTEL_LOGS_EXPORT_INTERVAL": "5000",
        "OTEL_METRICS_INCLUDE_SESSION_ID": "0",
        "OTEL_METRICS_INCLUDE_VERSION": "1",
        "OTEL_METRICS_INCLUDE_ACCOUNT_UUID": "0",
        "OTEL_LOG_USER_PROMPTS": "1",
        "OTEL_LOG_TOOL_DETAILS": "0",
        "OTEL_LOG_TOOL_CONTENT": "1",
        "OTEL_RESOURCE_ATTRIBUTES": "key%2C1=value%3D1,service.name=tracecat%20agent",
    }


def test_unset_agent_otel_fields_are_omitted_from_env() -> None:
    env = AgentOtelConfig(
        enabled=True, endpoint=AnyHttpUrl("https://collector.example.com")
    ).to_env()

    assert env == {
        "OTEL_EXPORTER_OTLP_ENDPOINT": "https://collector.example.com/",
        "OTEL_METRICS_EXPORTER": "otlp",
        "OTEL_LOGS_EXPORTER": "otlp",
        "OTEL_TRACES_EXPORTER": "none",
    }


def test_load_platform_override_parses_typed_config_and_headers() -> None:
    override = load_agent_otel_platform_override(
        config_json=('{"enabled":false,"logs_enabled":false,"log_tool_details":true}'),
        headers='{"x-api-key":"secret"}',
    )

    assert override is not None
    assert override.config.enabled is False
    assert override.config.logs_enabled is False
    assert override.config.log_tool_details is True
    assert override.headers["x-api-key"].get_secret_value() == "secret"


def test_load_platform_override_returns_none_when_config_unset() -> None:
    assert load_agent_otel_platform_override(config_json=None) is None
    assert load_agent_otel_platform_override(config_json="   ") is None


def test_load_platform_override_rejects_headers_in_config() -> None:
    with pytest.raises(ValueError, match="headers must be configured separately"):
        load_agent_otel_platform_override(
            config_json='{"enabled":true,"headers":{"x-api-key":"secret"}}'
        )


def test_load_platform_override_rejects_invalid_json() -> None:
    with pytest.raises(ValueError, match="platform OTel config must be valid JSON"):
        load_agent_otel_platform_override(config_json="{not json")


def test_load_platform_override_rejects_non_object_config() -> None:
    with pytest.raises(ValueError, match="platform OTel config must be a JSON object"):
        load_agent_otel_platform_override(config_json="[1, 2]")


def test_load_platform_override_rejects_non_object_headers() -> None:
    with pytest.raises(ValueError, match="platform OTel headers must be a JSON object"):
        load_agent_otel_platform_override(
            config_json='{"enabled":false}', headers='["x-api-key"]'
        )


def test_load_platform_override_rejects_empty_header_value() -> None:
    with pytest.raises(ValueError, match="must have a non-empty string value"):
        load_agent_otel_platform_override(
            config_json='{"enabled":false}', headers='{"x-api-key":""}'
        )


def test_load_platform_override_rejects_blank_header_name() -> None:
    with pytest.raises(ValueError, match="header names must be non-empty strings"):
        load_agent_otel_platform_override(
            config_json='{"enabled":false}', headers='{"  ":"secret"}'
        )
