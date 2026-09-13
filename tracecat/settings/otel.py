"""Security helpers for agent OTel settings."""

from __future__ import annotations

from tracecat.agent.otel_config import AgentOtelConfig
from tracecat.network import DisallowedUrlError, HttpOrigin
from tracecat.settings.schemas import AgentOtelSettingsUpdate


def bind_otel_headers_to_endpoint_origin(
    params: AgentOtelSettingsUpdate,
    *,
    current_config: object,
) -> AgentOtelSettingsUpdate:
    """Clear retained exporter headers when the collector origin changes.

    The headers field is write-only and may be omitted to retain its current
    value. Retention is safe only while the collector stays on the same origin;
    a cross-origin update must submit replacement headers explicitly.
    """
    if "agent_otel_headers" in params.model_fields_set:
        return params
    if "agent_otel_config" not in params.model_fields_set:
        return params

    current_endpoint = _configured_endpoint(current_config)
    next_endpoint = params.agent_otel_config.endpoint
    if _same_endpoint_origin(current_endpoint, next_endpoint):
        return params
    return params.model_copy(update={"agent_otel_headers": None})


def _configured_endpoint(config: object) -> object:
    if isinstance(config, AgentOtelConfig):
        return config.endpoint
    try:
        return AgentOtelConfig.model_validate(config).endpoint
    except (TypeError, ValueError):
        return None


def _same_endpoint_origin(first: object, second: object) -> bool:
    if first == second:
        return True
    if first is None or second is None:
        return False
    try:
        return HttpOrigin.from_url(str(first)) == HttpOrigin.from_url(str(second))
    except DisallowedUrlError:
        return False
