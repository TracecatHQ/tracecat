from collections.abc import Mapping
from typing import Any, cast
from uuid import uuid4

import pytest
from pydantic import HttpUrl, SecretStr, ValidationError

from tracecat.agent.otel_config import (
    AgentOtelConfig,
    AgentRunIdentity,
    resolve_agent_otel_config,
    secret_otel_headers,
    validate_otel_header_items,
)


def test_resolve_org_agent_otel_config_keeps_endpoint_host_side() -> None:
    resolved = resolve_agent_otel_config(
        org_config=AgentOtelConfig(
            enabled=True,
            endpoint=HttpUrl("https://collector.example.com"),
        ),
        org_headers={"Authorization": "Bearer token"},
    )

    assert resolved.enabled is True
    assert resolved.sandbox_env["CLAUDE_CODE_ENABLE_TELEMETRY"] == "1"
    # The shim is the endpoint's single writer; the resolver never emits one.
    assert "OTEL_EXPORTER_OTLP_ENDPOINT" not in resolved.sandbox_env
    assert resolved.sandbox_env["OTEL_EXPORTER_OTLP_PROTOCOL"] == "http/protobuf"
    assert (
        resolved.collector_env["OTEL_EXPORTER_OTLP_ENDPOINT"]
        == "https://collector.example.com/"
    )
    assert resolved.headers["Authorization"].get_secret_value() == "Bearer token"


def test_disabled_org_config_resolves_to_empty_envs() -> None:
    resolved = resolve_agent_otel_config(
        org_config=None,
        org_headers={"Authorization": "Bearer org"},
    )

    assert resolved.enabled is False
    assert resolved.sandbox_env == {}
    assert resolved.collector_env == {}
    assert resolved.headers == {}


def test_agent_otel_config_rejects_raw_env_map() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        AgentOtelConfig.model_validate(
            {"enabled": True, "env": {"OTEL_LOGS_EXPORTER": "console"}}
        )


def test_agent_otel_config_rejects_receiver_managed_protocol() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        AgentOtelConfig.model_validate({"protocol": "grpc"})


def test_agent_otel_config_rejects_exporter_lists() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        AgentOtelConfig.model_validate({"logs_exporters": ["otlp"]})


def test_agent_otel_config_rejects_non_positive_interval() -> None:
    with pytest.raises(ValidationError, match="greater than 0"):
        AgentOtelConfig(metric_export_interval_ms=0)


def test_agent_otel_config_rejects_empty_resource_attribute() -> None:
    with pytest.raises(ValidationError, match="cannot be empty"):
        AgentOtelConfig(resource_attributes={"service.name": "  "})


def test_agent_otel_config_rejects_reserved_resource_attribute() -> None:
    with pytest.raises(ValidationError, match="is reserved"):
        AgentOtelConfig(resource_attributes={"tracecat.session_id": "spoofed"})


def test_run_identity_attributes_reach_the_sandbox() -> None:
    session_id = uuid4()
    workspace_id = uuid4()
    organization_id = uuid4()
    user_id = uuid4()

    resolved = resolve_agent_otel_config(
        org_config=AgentOtelConfig(
            enabled=True,
            endpoint=HttpUrl("https://collector.example.com"),
            resource_attributes={"service.name": "tracecat agent"},
        ),
        org_headers=None,
        run_identity=AgentRunIdentity(
            session_id=session_id,
            workspace_id=workspace_id,
            organization_id=organization_id,
            user_id=user_id,
        ),
    )

    attributes = dict(
        pair.split("=", 1)
        for pair in resolved.sandbox_env["OTEL_RESOURCE_ATTRIBUTES"].split(",")
    )
    assert attributes == {
        "service.name": "tracecat%20agent",
        "tracecat.session_id": str(session_id),
        "tracecat.workspace_id": str(workspace_id),
        "tracecat.organization_id": str(organization_id),
        "tracecat.user_id": str(user_id),
    }


def test_run_identity_omits_absent_user() -> None:
    identity = AgentRunIdentity(session_id=uuid4(), workspace_id=uuid4())

    assert set(identity.to_resource_attributes()) == {
        "tracecat.session_id",
        "tracecat.workspace_id",
    }


def test_agent_otel_config_rejects_endpoint_userinfo() -> None:
    with pytest.raises(ValidationError, match="must not embed credentials"):
        AgentOtelConfig(
            enabled=True,
            endpoint=HttpUrl("https://user:password@collector.example.com"),
        )


def test_agent_otel_config_rejects_endpoint_query_string() -> None:
    with pytest.raises(ValidationError, match="must not include a query string"):
        AgentOtelConfig(
            enabled=True,
            endpoint=HttpUrl("https://collector.example.com/v1?api-key=secret"),
        )


def test_agent_otel_config_rejects_endpoint_fragment() -> None:
    # Fragments are never sent over HTTP, so signal paths appended after one
    # would deliver to the wrong URL.
    with pytest.raises(ValidationError, match="must not include a fragment"):
        AgentOtelConfig(
            enabled=True,
            endpoint=HttpUrl("https://collector.example.com/base#fragment"),
        )


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
        endpoint=HttpUrl("https://collector.example.com"),
        traces_enabled=True,
    ).to_env()

    assert env["OTEL_TRACES_EXPORTER"] == "otlp"
    assert env["CLAUDE_CODE_ENHANCED_TELEMETRY_BETA"] == "1"


def test_disabled_traces_do_not_set_beta_flag() -> None:
    env = AgentOtelConfig(
        enabled=True, endpoint=HttpUrl("https://collector.example.com")
    ).to_env()

    assert env["OTEL_TRACES_EXPORTER"] == "none"
    assert "CLAUDE_CODE_ENHANCED_TELEMETRY_BETA" not in env


def test_agent_otel_config_serializes_typed_fields_to_env() -> None:
    otel_config = AgentOtelConfig(
        enabled=True,
        endpoint=HttpUrl("https://collector.example.com:4318"),
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
        enabled=True, endpoint=HttpUrl("https://collector.example.com")
    ).to_env()

    assert env == {
        "OTEL_EXPORTER_OTLP_ENDPOINT": "https://collector.example.com/",
        "OTEL_METRICS_EXPORTER": "otlp",
        "OTEL_LOGS_EXPORTER": "otlp",
        "OTEL_TRACES_EXPORTER": "none",
    }


def test_secret_otel_headers_wraps_values() -> None:
    headers = secret_otel_headers(
        {"x-api-key": "secret", "Authorization": SecretStr("Bearer token")}
    )

    assert headers["x-api-key"].get_secret_value() == "secret"
    assert headers["Authorization"].get_secret_value() == "Bearer token"


def test_secret_otel_headers_returns_empty_for_none() -> None:
    assert secret_otel_headers(None) == {}


@pytest.mark.parametrize(
    "headers",
    [
        {"Bad Header": "x"},
        {"Authorization\n": "x"},
        {"Auth\u00e9": "x"},
        {"Authorization": "bad\nvalue"},
        {"Authorization": "  edge  "},
        {"Authorization": "bad\x01value"},
        # Non-ASCII: httpx ASCII-encodes header values at request build time.
        {"Authorization": "café"},
    ],
)
def test_validate_otel_header_items_rejects_unsendable(
    headers: dict[str, str],
) -> None:
    with pytest.raises(ValueError):
        validate_otel_header_items(headers)


def test_validate_otel_header_items_accepts_token_names() -> None:
    validate_otel_header_items(
        {"Authorization": "Bearer token", "X-Api-Key": "k", "x_custom": "v"}
    )
    assert secret_otel_headers({}) == {}


def test_validate_otel_header_items_rejects_empty_header_value() -> None:
    with pytest.raises(ValueError, match="must have a non-empty string value"):
        validate_otel_header_items({"x-api-key": ""})


def test_validate_otel_header_items_rejects_blank_header_value() -> None:
    with pytest.raises(ValueError, match="must have a non-empty string value"):
        validate_otel_header_items({"x-api-key": "   "})


def test_validate_otel_header_items_rejects_blank_secret_header_value() -> None:
    with pytest.raises(ValueError, match="must have a non-empty string value"):
        validate_otel_header_items({"x-api-key": SecretStr("   ")})


def test_validate_otel_header_items_rejects_non_string_header_value() -> None:
    with pytest.raises(ValueError, match="must have a non-empty string value"):
        validate_otel_header_items({"x-api-key": 1})


def test_validate_otel_header_items_rejects_blank_header_name() -> None:
    with pytest.raises(
        ValueError, match="header names must be valid HTTP header names"
    ):
        validate_otel_header_items({"  ": "secret"})


def test_validate_otel_header_items_rejects_non_string_header_name() -> None:
    # Non-string keys can only arrive from untyped deserialized input.
    headers = cast(Mapping[str, Any], {1: "secret"})
    with pytest.raises(
        ValueError, match="header names must be valid HTTP header names"
    ):
        validate_otel_header_items(headers)


def test_secret_otel_headers_rejects_malformed_headers() -> None:
    with pytest.raises(ValueError, match="must have a non-empty string value"):
        secret_otel_headers({"x-api-key": "   "})
    with pytest.raises(
        ValueError, match="header names must be valid HTTP header names"
    ):
        secret_otel_headers({"": "secret"})


@pytest.mark.parametrize(
    ("endpoint", "message"),
    [
        ("https://collector.example.com/base?", "must not include a query string"),
        ("https://collector.example.com/base#", "must not include a fragment"),
    ],
)
def test_endpoint_rejects_bare_query_and_fragment_delimiters(
    endpoint: str, message: str
) -> None:
    # A bare delimiter parses as an empty query/fragment; the relay would then
    # append the OTLP signal path after it and deliver to the wrong path.
    with pytest.raises(ValidationError, match=message):
        AgentOtelConfig.model_validate({"enabled": True, "endpoint": endpoint})
