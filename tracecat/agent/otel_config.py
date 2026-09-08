import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Any, Literal, Self
from urllib.parse import quote
from uuid import UUID

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

from tracecat.identifiers import OrganizationID, UserID, WorkspaceID

OtelProtocol = Literal["grpc", "http/json", "http/protobuf"]
MetricsTemporality = Literal["delta", "cumulative"]

# Claude Code's own `session.id` and `user.id` identify its process and its
# per-install anonymous identity, neither of which a workspace can resolve back
# to anything it configured. Tracecat's own identifiers travel under this
# reserved namespace instead, so org config may not define keys under it.
RESERVED_ATTRIBUTE_PREFIX = "tracecat."


def _reject_endpoint_credentials(value: HttpUrl) -> HttpUrl:
    # agent_otel_config is stored unencrypted and echoed by the read API, so
    # credentials must only travel through the encrypted headers field.
    if value.username or value.password:
        raise ValueError(
            "endpoint must not embed credentials; use exporter headers instead"
        )
    if value.query is not None:
        raise ValueError(
            "endpoint must not include a query string; use exporter headers "
            "for authentication"
        )
    if value.fragment is not None:
        raise ValueError("endpoint must not include a fragment")
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
    endpoint: OtlpEndpoint | None = Field(
        default=None, description="OTLP collector endpoint for all signals."
    )

    # Signals
    metrics_enabled: bool = Field(
        default=True, description="Whether metrics are exported."
    )
    logs_enabled: bool = Field(
        default=True, description="Whether logs and events are exported."
    )
    traces_enabled: bool = Field(
        default=False,
        description="Whether traces are exported. Enables Claude Code beta tracing.",
    )
    metrics_temporality: MetricsTemporality | None = Field(
        default=None, description="Metrics aggregation temporality."
    )
    metric_export_interval_ms: PositiveInt | None = Field(
        default=None, description="Metrics export interval in milliseconds."
    )
    logs_export_interval_ms: PositiveInt | None = Field(
        default=None, description="Logs export interval in milliseconds."
    )

    # Privacy and metric cardinality
    metrics_include_session_id: bool | None = Field(
        default=None,
        description="Whether metrics include the Claude Code session identifier.",
    )
    metrics_include_version: bool | None = Field(
        default=None, description="Whether metrics include the Claude Code version."
    )
    metrics_include_account_uuid: bool | None = Field(
        default=None,
        description="Whether metrics include the authenticated account identifier.",
    )
    log_user_prompts: bool | None = Field(
        default=None, description="Whether telemetry includes user prompt content."
    )
    log_tool_details: bool | None = Field(
        default=None,
        description="Whether telemetry includes tool parameters and input arguments.",
    )
    log_tool_content: bool | None = Field(
        default=None,
        description="Whether telemetry includes tool input and output content.",
    )

    # Resource
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
            if key.startswith(RESERVED_ATTRIBUTE_PREFIX):
                raise ValueError(
                    f"resource attribute {key} is reserved; "
                    f"{RESERVED_ATTRIBUTE_PREFIX}* names are set per agent run"
                )
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
        _set_env(env, "OTEL_EXPORTER_OTLP_ENDPOINT", self.endpoint)

        # OTLP via the socket receiver is the only egress from the sandbox; pull-based and
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
class AgentRunIdentity:
    """Tracecat identifiers stamped onto one agent run's telemetry.

    ``session_id`` is the same identifier a workflow reads back from an agent
    action result, so exported telemetry joins to the run that produced it.
    """

    session_id: UUID
    workspace_id: WorkspaceID
    organization_id: OrganizationID | None = None
    user_id: UserID | None = None

    def to_resource_attributes(self) -> dict[str, str]:
        """Serialize the identity as OTel resource attributes.

        Returns:
            Attribute names under the reserved Tracecat namespace, omitting
            identifiers the run has no value for.
        """
        attributes = {
            "tracecat.session_id": str(self.session_id),
            "tracecat.workspace_id": str(self.workspace_id),
        }
        if self.organization_id is not None:
            attributes["tracecat.organization_id"] = str(self.organization_id)
        if self.user_id is not None:
            attributes["tracecat.user_id"] = str(self.user_id)
        return attributes


class ResolvedAgentOtelConfig(BaseModel):
    """Single source of truth used by the agent runtime."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(default=False)
    sandbox_env: dict[str, str] = Field(default_factory=dict)
    collector_env: dict[str, str] = Field(default_factory=dict)
    headers: dict[str, SecretStr] = Field(default_factory=dict)


def resolve_agent_otel_config(
    *,
    org_config: AgentOtelConfig | None,
    org_headers: Mapping[str, str] | None,
    run_identity: AgentRunIdentity | None = None,
) -> ResolvedAgentOtelConfig:
    """Resolve org OTel inputs into one runtime config.

    `sandbox_env` never carries a collector endpoint: the in-sandbox shim
    points the exporter at its local bridge, and the collector details stay
    host-side in `collector_env`. When a run identity is supplied its
    attributes are merged into the sandbox resource attributes, which Claude
    Code attaches to every metric datapoint, event record, and span.
    """
    config_value = org_config or AgentOtelConfig()
    headers = secret_otel_headers(org_headers)

    if not config_value.enabled:
        return ResolvedAgentOtelConfig(enabled=False)

    collector_env = config_value.to_env()
    sandbox_env = _build_sandbox_env(collector_env)
    sandbox_env["CLAUDE_CODE_ENABLE_TELEMETRY"] = "1"
    if run_identity is not None:
        sandbox_env["OTEL_RESOURCE_ATTRIBUTES"] = _serialize_resource_attributes(
            {
                **config_value.resource_attributes,
                **run_identity.to_resource_attributes(),
            }
        )
    return ResolvedAgentOtelConfig(
        enabled=True,
        sandbox_env=sandbox_env,
        collector_env=collector_env,
        headers=headers,
    )


# RFC 7230 token: the only characters legal in an HTTP header name.
_HEADER_NAME_RE = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+")


def _invalid_header_value(value: str) -> bool:
    # CR/LF, control bytes, edge whitespace, or non-ASCII make httpx reject
    # the request client-side, silently killing every delivery.
    return value != value.strip() or any(
        ord(ch) < 0x20 or ord(ch) > 0x7E for ch in value
    )


def validate_otel_header_items(headers: Mapping[str, Any]) -> None:
    """Reject header names that are not HTTP tokens and unsendable values."""
    for key, value in headers.items():
        if not isinstance(key, str) or not _HEADER_NAME_RE.fullmatch(key):
            raise ValueError(
                "OTel header names must be valid HTTP header names "
                "(letters, digits, and !#$%&'*+-.^_`|~)"
            )
        raw_value = value.get_secret_value() if isinstance(value, SecretStr) else value
        if not isinstance(raw_value, str) or not raw_value.strip():
            raise ValueError(f"OTel header {key} must have a non-empty string value")
        if _invalid_header_value(raw_value):
            raise ValueError(
                f"OTel header {key} value must contain only printable ASCII "
                "without leading/trailing whitespace"
            )


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


def _build_sandbox_env(env: dict[str, str]) -> dict[str, str]:
    # The shim is the endpoint's single writer: it sets the exporter endpoint
    # to its in-sandbox bridge after the bridge binds.
    sandbox_env = dict(env)
    sandbox_env.pop("OTEL_EXPORTER_OTLP_ENDPOINT", None)
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
