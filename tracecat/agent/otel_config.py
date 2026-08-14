from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Any, Literal, Self
from urllib.parse import quote

import orjson
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    PositiveInt,
    SecretStr,
    field_validator,
    model_validator,
)

from tracecat import config

OtelProtocol = Literal["grpc", "http/json", "http/protobuf"]
MetricsTemporality = Literal["delta", "cumulative"]


def _reject_endpoint_credentials(value: HttpUrl) -> HttpUrl:
    # agent_otel_config is stored unencrypted and echoed by the read API, so
    # credentials must only travel through the encrypted headers field.
    if value.username or value.password:
        raise ValueError(
            "endpoint must not embed credentials; use exporter headers instead"
        )
    if value.query:
        raise ValueError(
            "endpoint must not include a query string; use exporter headers "
            "for authentication"
        )
    return value


OtlpEndpoint = Annotated[HttpUrl, AfterValidator(_reject_endpoint_credentials)]


class AgentOtelConfig(BaseModel):
    """Organization-scoped Claude Code OTel configuration.

    See https://code.claude.com/docs/en/monitoring-usage for the env vars
    these fields map onto.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    enabled: bool = Field(
        default=False,
        description="Whether Claude Code telemetry is enabled for agent runs.",
    )
    protocol: OtelProtocol | None = Field(
        default=None,
        description="OTLP transport protocol for all signals.",
    )
    endpoint: OtlpEndpoint | None = Field(
        default=None,
        description="OTLP collector endpoint for all signals.",
    )
    metrics_enabled: bool = Field(
        default=True,
        description="Whether metrics are exported.",
    )
    logs_enabled: bool = Field(
        default=True,
        description="Whether logs and events are exported.",
    )
    traces_enabled: bool = Field(
        default=False,
        description="Whether traces are exported. Enables Claude Code beta tracing.",
    )
    metrics_temporality: MetricsTemporality | None = Field(
        default=None,
        description="Metrics aggregation temporality.",
    )
    metric_export_interval_ms: PositiveInt | None = Field(
        default=None,
        description="Metrics export interval in milliseconds.",
    )
    logs_export_interval_ms: PositiveInt | None = Field(
        default=None,
        description="Logs export interval in milliseconds.",
    )
    metrics_include_session_id: bool | None = Field(
        default=None,
        description="Whether metrics include the Claude Code session identifier.",
    )
    metrics_include_version: bool | None = Field(
        default=None,
        description="Whether metrics include the Claude Code version.",
    )
    metrics_include_account_uuid: bool | None = Field(
        default=None,
        description="Whether metrics include the authenticated account identifier.",
    )
    log_user_prompts: bool | None = Field(
        default=None,
        description="Whether telemetry includes user prompt content.",
    )
    log_tool_details: bool | None = Field(
        default=None,
        description="Whether telemetry includes tool parameters and input arguments.",
    )
    log_tool_content: bool | None = Field(
        default=None,
        description="Whether telemetry includes tool input and output content.",
    )
    resource_attributes: dict[str, str] = Field(
        default_factory=dict,
        description="Resource attributes attached to exported telemetry.",
    )

    @field_validator("resource_attributes")
    @classmethod
    def validate_resource_attributes(cls, value: dict[str, str]) -> dict[str, str]:
        # Keys and values are already whitespace-stripped by str_strip_whitespace.
        for key, attribute_value in value.items():
            if not key:
                raise ValueError("resource attribute names cannot be empty")
            if not attribute_value:
                raise ValueError(f"resource attribute {key} cannot be empty")
        return value

    @model_validator(mode="after")
    def validate_otlp_endpoint(self) -> Self:
        if (
            self.enabled
            and (self.metrics_enabled or self.logs_enabled or self.traces_enabled)
            and self.endpoint is None
        ):
            raise ValueError("telemetry is enabled but no endpoint is configured")
        return self

    def to_env(self) -> dict[str, str]:
        """Serialize validated configuration for the Claude Code process."""
        env: dict[str, str] = {}
        _set_env(env, "OTEL_EXPORTER_OTLP_PROTOCOL", self.protocol)
        _set_env(env, "OTEL_EXPORTER_OTLP_ENDPOINT", self.endpoint)

        # OTLP via the relay is the only egress from the sandbox; pull-based and
        # console exporters are not supported.
        env["OTEL_METRICS_EXPORTER"] = "otlp" if self.metrics_enabled else "none"
        env["OTEL_LOGS_EXPORTER"] = "otlp" if self.logs_enabled else "none"
        env["OTEL_TRACES_EXPORTER"] = "otlp" if self.traces_enabled else "none"
        if self.traces_enabled:
            # Claude Code emits traces only with this beta flag; owned here so it
            # never becomes API surface.
            env["CLAUDE_CODE_ENHANCED_TELEMETRY_BETA"] = "1"

        _set_env(
            env,
            "OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE",
            self.metrics_temporality,
        )
        _set_env(env, "OTEL_METRIC_EXPORT_INTERVAL", self.metric_export_interval_ms)
        _set_env(env, "OTEL_LOGS_EXPORT_INTERVAL", self.logs_export_interval_ms)
        _set_env(
            env, "OTEL_METRICS_INCLUDE_SESSION_ID", self.metrics_include_session_id
        )
        _set_env(env, "OTEL_METRICS_INCLUDE_VERSION", self.metrics_include_version)
        _set_env(
            env, "OTEL_METRICS_INCLUDE_ACCOUNT_UUID", self.metrics_include_account_uuid
        )
        _set_env(env, "OTEL_LOG_USER_PROMPTS", self.log_user_prompts)
        _set_env(env, "OTEL_LOG_TOOL_DETAILS", self.log_tool_details)
        _set_env(env, "OTEL_LOG_TOOL_CONTENT", self.log_tool_content)

        if self.resource_attributes:
            env["OTEL_RESOURCE_ATTRIBUTES"] = _serialize_resource_attributes(
                self.resource_attributes
            )
        return env


@dataclass(frozen=True, slots=True)
class AgentOtelPlatformOverride:
    """Platform-wide OTel override for self-hosted deployments."""

    config: AgentOtelConfig
    headers: dict[str, SecretStr]


class ResolvedAgentOtelConfig(BaseModel):
    """Single source of truth used by the agent runtime."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(default=False)
    sandbox_env: dict[str, str] = Field(default_factory=dict)
    collector_env: dict[str, str] = Field(default_factory=dict)
    headers: dict[str, SecretStr] = Field(default_factory=dict)
    source: Literal["org", "platform"] = Field(default="org")


def resolve_agent_otel_config(
    *,
    org_config: AgentOtelConfig | None,
    org_headers: Mapping[str, str] | None,
    platform_override: AgentOtelPlatformOverride | None,
    relay_endpoint: str | None = None,
) -> ResolvedAgentOtelConfig:
    """Resolve platform and org OTel inputs into one runtime config.

    Platform override wins wholesale when present. When a relay endpoint is
    supplied, sandbox traffic is redirected to the local relay and the original
    collector details are kept in `collector_env`.
    """
    if platform_override is not None:
        source: Literal["org", "platform"] = "platform"
        config_value = platform_override.config
        headers = dict(platform_override.headers)
    else:
        source = "org"
        config_value = org_config or AgentOtelConfig()
        headers = secret_otel_headers(org_headers)

    if not config_value.enabled:
        return ResolvedAgentOtelConfig(enabled=False, source=source)

    collector_env = config_value.to_env()
    sandbox_env = _build_sandbox_env(collector_env, relay_endpoint=relay_endpoint)
    sandbox_env["CLAUDE_CODE_ENABLE_TELEMETRY"] = "1"
    return ResolvedAgentOtelConfig(
        enabled=True,
        sandbox_env=sandbox_env,
        collector_env=collector_env,
        headers=headers,
        source=source,
    )


def load_agent_otel_platform_override(
    *,
    config_json: str | None = config.TRACECAT__AGENT_OTEL_PLATFORM_OVERRIDE_CONFIG,
    headers: str | None = config.TRACECAT__AGENT_OTEL_PLATFORM_OVERRIDE_HEADERS,
) -> AgentOtelPlatformOverride | None:
    """Load the optional platform override through the typed OTel contract."""
    if config_json is None or not config_json.strip():
        return None

    # JSON is untyped only at this deployment boundary. The DTO validates it next.
    config_data = _parse_json_object(config_json, name="platform OTel config")
    if "headers" in config_data:
        raise ValueError("platform OTel headers must be configured separately")
    header_data = _parse_json_object(headers, name="platform OTel headers")
    return AgentOtelPlatformOverride(
        config=AgentOtelConfig.model_validate(config_data),
        headers=secret_otel_headers(header_data),
    )


def validate_otel_header_items(headers: Mapping[str, Any]) -> None:
    """Reject empty header names or non-string/empty header values."""
    for key, value in headers.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError("OTel header names must be non-empty strings")
        raw_value = value.get_secret_value() if isinstance(value, SecretStr) else value
        if not isinstance(raw_value, str) or not raw_value:
            raise ValueError(f"OTel header {key} must have a non-empty string value")


def secret_otel_headers(
    headers: Mapping[str, str | SecretStr] | None,
) -> dict[str, SecretStr]:
    if not headers:
        return {}
    validate_otel_header_items(headers)
    return {
        key: value if isinstance(value, SecretStr) else SecretStr(value)
        for key, value in headers.items()
    }


def _build_sandbox_env(
    env: dict[str, str], *, relay_endpoint: str | None
) -> dict[str, str]:
    sandbox_env = dict(env)
    if relay_endpoint is None:
        return sandbox_env

    sandbox_env["OTEL_EXPORTER_OTLP_ENDPOINT"] = relay_endpoint
    sandbox_env["OTEL_EXPORTER_OTLP_PROTOCOL"] = "http/protobuf"
    return sandbox_env


def _set_env(
    env: dict[str, str],
    key: str,
    value: str | int | bool | HttpUrl | None,
) -> None:
    if value is None:
        return
    if isinstance(value, bool):
        env[key] = "1" if value else "0"
        return
    env[key] = str(value)


def _serialize_resource_attributes(attributes: Mapping[str, str]) -> str:
    return ",".join(
        f"{quote(key, safe='')}={quote(value, safe='')}"
        for key, value in sorted(attributes.items())
    )


def _parse_json_object(raw: str | None, *, name: str) -> dict[str, Any]:
    if raw is None or raw == "":
        return {}
    try:
        value = orjson.loads(raw)
    except orjson.JSONDecodeError as e:
        raise ValueError(f"{name} must be valid JSON") from e
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a JSON object")
    return value
