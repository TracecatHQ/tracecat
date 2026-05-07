from collections.abc import Mapping
from typing import Any, Literal

import orjson
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from tracecat import config

AGENT_OTEL_ENV_VARS: frozenset[str] = frozenset(
    {
        "OTEL_METRICS_EXPORTER",
        "OTEL_LOGS_EXPORTER",
        "OTEL_TRACES_EXPORTER",
        "OTEL_EXPORTER_OTLP_PROTOCOL",
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OTEL_EXPORTER_OTLP_METRICS_PROTOCOL",
        "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT",
        "OTEL_EXPORTER_OTLP_LOGS_PROTOCOL",
        "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT",
        "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        "CLAUDE_CODE_OTEL_HEADERS_HELPER_DEBOUNCE_MS",
        "OTEL_METRIC_EXPORT_INTERVAL",
        "OTEL_LOGS_EXPORT_INTERVAL",
        "OTEL_TRACES_EXPORT_INTERVAL",
        "CLAUDE_CODE_ENHANCED_TELEMETRY_BETA",
        "ENABLE_ENHANCED_TELEMETRY_BETA",
        "ENABLE_BETA_TRACING_DETAILED",
        "BETA_TRACING_ENDPOINT",
        "OTEL_LOG_USER_PROMPTS",
        "OTEL_LOG_TOOL_DETAILS",
        "OTEL_LOG_TOOL_CONTENT",
        "OTEL_LOG_RAW_API_BODIES",
        "OTEL_METRICS_INCLUDE_SESSION_ID",
        "OTEL_METRICS_INCLUDE_VERSION",
        "OTEL_METRICS_INCLUDE_ACCOUNT_UUID",
        "OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE",
        "OTEL_RESOURCE_ATTRIBUTES",
    }
)
"""Claude Code monitoring environment variables Tracecat allows users to configure."""

_RESERVED_AGENT_OTEL_ENV_VARS = frozenset(
    {
        "CLAUDE_CODE_ENABLE_TELEMETRY",
        "OTEL_EXPORTER_OTLP_HEADERS",
        "OTEL_EXPORTER_OTLP_METRICS_HEADERS",
        "OTEL_EXPORTER_OTLP_LOGS_HEADERS",
        "OTEL_EXPORTER_OTLP_TRACES_HEADERS",
    }
)
"""Supported semantics that are modeled outside the raw env map."""

_EXPORTER_VALUES = {
    "OTEL_METRICS_EXPORTER": frozenset({"console", "otlp", "prometheus", "none"}),
    "OTEL_LOGS_EXPORTER": frozenset({"console", "otlp", "none"}),
    "OTEL_TRACES_EXPORTER": frozenset({"console", "otlp", "none"}),
}
_PROTOCOL_KEYS = frozenset(
    {
        "OTEL_EXPORTER_OTLP_PROTOCOL",
        "OTEL_EXPORTER_OTLP_METRICS_PROTOCOL",
        "OTEL_EXPORTER_OTLP_LOGS_PROTOCOL",
        "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL",
    }
)
_PROTOCOL_VALUES = frozenset({"grpc", "http/json", "http/protobuf"})
_INTERVAL_KEYS = frozenset(
    {
        "CLAUDE_CODE_OTEL_HEADERS_HELPER_DEBOUNCE_MS",
        "OTEL_METRIC_EXPORT_INTERVAL",
        "OTEL_LOGS_EXPORT_INTERVAL",
        "OTEL_TRACES_EXPORT_INTERVAL",
    }
)
_TEMPORALITY_VALUES = frozenset({"delta", "cumulative"})
_SIGNAL_EXPORTER_KEYS = {
    "metrics": "OTEL_METRICS_EXPORTER",
    "logs": "OTEL_LOGS_EXPORTER",
    "traces": "OTEL_TRACES_EXPORTER",
}
_SIGNAL_ENDPOINT_KEYS = {
    "metrics": "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT",
    "logs": "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT",
    "traces": "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
}
_SIGNAL_PROTOCOL_KEYS = {
    "metrics": "OTEL_EXPORTER_OTLP_METRICS_PROTOCOL",
    "logs": "OTEL_EXPORTER_OTLP_LOGS_PROTOCOL",
    "traces": "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL",
}


class AgentOtelConfig(BaseModel):
    """Organization-scoped Claude Code OTel configuration."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        default=False,
        description="Whether Claude Code telemetry is enabled for agent runs.",
    )
    env: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Allowlisted Claude Code OTel environment variables. "
            "Headers are configured separately."
        ),
    )

    @field_validator("env")
    @classmethod
    def validate_env(cls, value: dict[str, str]) -> dict[str, str]:
        return validate_agent_otel_env(value)


class AgentOtelPlatformOverride(AgentOtelConfig):
    """Platform-wide OTel override for self-hosted deployments."""

    headers: dict[str, SecretStr] = Field(default_factory=dict)

    @field_validator("headers", mode="before")
    @classmethod
    def validate_headers(
        cls, value: Mapping[str, str | SecretStr] | None
    ) -> dict[str, SecretStr]:
        return _secret_headers(value)


class ResolvedAgentOtelConfig(BaseModel):
    """Single source of truth used by the agent runtime."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(default=False)
    sandbox_env: dict[str, str] = Field(default_factory=dict)
    collector_env: dict[str, str] = Field(default_factory=dict)
    headers: dict[str, SecretStr] = Field(default_factory=dict)
    source: Literal["org", "platform"] = Field(default="org")
    relay_timeout_seconds: float = Field(default=10.0)


def validate_agent_otel_env(env: Mapping[str, str]) -> dict[str, str]:
    """Validate and normalize user-provided Claude Code OTel environment values."""
    normalized: dict[str, str] = {}
    for key, value in env.items():
        if key in _RESERVED_AGENT_OTEL_ENV_VARS:
            raise ValueError(f"{key} is modeled separately and cannot be set in env")
        if key not in AGENT_OTEL_ENV_VARS:
            raise ValueError(
                f"Unsupported Claude Code OTel environment variable: {key}"
            )
        if not isinstance(value, str):
            raise ValueError(f"{key} must be a string value")

        value = value.strip()
        if not value:
            raise ValueError(f"{key} cannot be empty")
        _validate_agent_otel_env_value(key, value)
        normalized[key] = value
    return normalized


def resolve_agent_otel_config(
    *,
    org_config: AgentOtelConfig | None,
    org_headers: Mapping[str, str] | None,
    platform_override: AgentOtelPlatformOverride | None,
    relay_endpoint: str | None = None,
    relay_timeout_seconds: float = config.TRACECAT__AGENT_OTEL_RELAY_TIMEOUT_SECONDS,
) -> ResolvedAgentOtelConfig:
    """Resolve platform and org OTel inputs into one runtime config.

    Platform override wins wholesale when present. When a relay endpoint is
    supplied, sandbox traffic is redirected to the local relay and the original
    collector details are kept in `collector_env`.
    """
    if platform_override is not None:
        source: Literal["org", "platform"] = "platform"
        enabled = platform_override.enabled
        env = dict(platform_override.env)
        headers = dict(platform_override.headers)
    else:
        source = "org"
        config_value = org_config or AgentOtelConfig()
        enabled = config_value.enabled
        env = dict(config_value.env)
        headers = _secret_headers(org_headers)

    if not enabled:
        return ResolvedAgentOtelConfig(
            enabled=False,
            source=source,
            relay_timeout_seconds=relay_timeout_seconds,
        )

    _validate_otlp_endpoint_requirements(env)
    sandbox_env = _build_sandbox_env(env, relay_endpoint=relay_endpoint)
    sandbox_env["CLAUDE_CODE_ENABLE_TELEMETRY"] = "1"
    return ResolvedAgentOtelConfig(
        enabled=True,
        sandbox_env=sandbox_env,
        collector_env=env,
        headers=headers,
        source=source,
        relay_timeout_seconds=relay_timeout_seconds,
    )


async def load_org_agent_otel_inputs(
    *,
    role: Any,
) -> tuple[AgentOtelConfig | None, dict[str, str] | None]:
    """Load the org's saved Agent OTel config and decrypted headers.

    Returns ``(None, None)`` when the org has no settings rows yet so the
    resolver can fall back to its defaults.
    """
    from tracecat.settings.service import SettingsService

    async with SettingsService.with_session(role=role) as service:
        settings = await service.list_org_settings(
            keys={"agent_otel_config", "agent_otel_headers"}
        )
        values, _ = service.get_values_with_decryption_fallback(settings)

    raw_config = values.get("agent_otel_config")
    config_value: AgentOtelConfig | None = None
    if isinstance(raw_config, dict):
        config_value = AgentOtelConfig.model_validate(raw_config)

    raw_headers = values.get("agent_otel_headers")
    headers_value: dict[str, str] | None = None
    if isinstance(raw_headers, dict):
        headers_value = {str(k): str(v) for k, v in raw_headers.items()}

    return config_value, headers_value


def load_agent_otel_platform_override(
    *,
    enabled: str | None = config.TRACECAT__AGENT_OTEL_PLATFORM_OVERRIDE_ENABLED,
    env: str | None = config.TRACECAT__AGENT_OTEL_PLATFORM_OVERRIDE_ENV,
    headers: str | None = config.TRACECAT__AGENT_OTEL_PLATFORM_OVERRIDE_HEADERS,
) -> AgentOtelPlatformOverride | None:
    """Load platform override config from raw environment values.

    An unset `enabled` value means no platform override exists. Set it to either
    true or false to definitively override org-level configuration.
    """
    if enabled is None or enabled == "":
        return None

    return AgentOtelPlatformOverride(
        enabled=_parse_bool(enabled, name="platform override enabled"),
        env=_parse_json_object(env, name="platform OTel env"),
        headers=_secret_headers(
            _parse_json_object(headers, name="platform OTel headers")
        ),
    )


def _build_sandbox_env(
    env: dict[str, str], *, relay_endpoint: str | None
) -> dict[str, str]:
    if relay_endpoint is None:
        return dict(env)

    sandbox_env = dict(env)
    sandbox_env["OTEL_EXPORTER_OTLP_ENDPOINT"] = relay_endpoint
    sandbox_env["OTEL_EXPORTER_OTLP_PROTOCOL"] = "http/protobuf"
    for endpoint_key in _SIGNAL_ENDPOINT_KEYS.values():
        sandbox_env.pop(endpoint_key, None)
    for protocol_key in _SIGNAL_PROTOCOL_KEYS.values():
        sandbox_env.pop(protocol_key, None)
    return sandbox_env


def _validate_agent_otel_env_value(key: str, value: str) -> None:
    if allowed_exporters := _EXPORTER_VALUES.get(key):
        exporters = _split_csv(value)
        invalid_exporters = exporters - allowed_exporters
        if invalid_exporters:
            raise ValueError(
                f"{key} contains unsupported exporter(s): {', '.join(sorted(invalid_exporters))}"
            )
        return

    if key in _PROTOCOL_KEYS and value not in _PROTOCOL_VALUES:
        raise ValueError(f"{key} must be one of: {', '.join(sorted(_PROTOCOL_VALUES))}")

    if key in _INTERVAL_KEYS:
        _parse_positive_int(value, name=key)

    if (
        key == "OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE"
        and value.lower() not in _TEMPORALITY_VALUES
    ):
        raise ValueError(
            f"{key} must be one of: {', '.join(sorted(_TEMPORALITY_VALUES))}"
        )


def _validate_otlp_endpoint_requirements(env: Mapping[str, str]) -> None:
    generic_endpoint = env.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    for signal, exporter_key in _SIGNAL_EXPORTER_KEYS.items():
        exporters = _split_csv(env.get(exporter_key, ""))
        if "otlp" not in exporters:
            continue
        signal_endpoint_key = _SIGNAL_ENDPOINT_KEYS[signal]
        if not generic_endpoint and not env.get(signal_endpoint_key):
            raise ValueError(
                f"{exporter_key}=otlp requires OTEL_EXPORTER_OTLP_ENDPOINT "
                f"or {signal_endpoint_key}"
            )


def _split_csv(value: str) -> set[str]:
    return {part.strip().lower() for part in value.split(",") if part.strip()}


def _parse_positive_int(value: str, *, name: str) -> int:
    try:
        parsed = int(value)
    except ValueError as e:
        raise ValueError(f"{name} must be an integer") from e
    if parsed <= 0:
        raise ValueError(f"{name} must be greater than 0")
    return parsed


def _parse_bool(value: str, *, name: str) -> bool:
    match value.strip().lower():
        case "true" | "1":
            return True
        case "false" | "0":
            return False
        case _:
            raise ValueError(f"{name} must be true or false")


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


def _secret_headers(
    headers: Mapping[str, str | SecretStr] | None,
) -> dict[str, SecretStr]:
    if not headers:
        return {}
    secret_headers: dict[str, SecretStr] = {}
    for key, value in headers.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError("OTel header names must be non-empty strings")
        raw_value = value.get_secret_value() if isinstance(value, SecretStr) else value
        if not isinstance(raw_value, str) or not raw_value:
            raise ValueError(f"OTel header {key} must have a non-empty string value")
        secret_headers[key] = SecretStr(raw_value)
    return secret_headers
