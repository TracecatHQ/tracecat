"""Tests for tracecat.dsl._converter payload sanitization."""

from __future__ import annotations

import pytest
import temporalio.api.common.v1
from temporalio.api.common.v1 import Payload
from tracecat_registry.sdk.agents import AgentConfig as RegistryAgentConfig

from tracecat.agent.types import AgentConfig as TracecatAgentConfig
from tracecat.agent.workflow_schemas import AgentConfigPayload
from tracecat.dsl._converter import PydanticORJSONPayloadConverter
from tracecat.dsl.common import AgentActionMemo


class _LeakyValue:
    def __repr__(self) -> str:
        return "Bearer secret-token"

    def __str__(self) -> str:
        return "Bearer secret-token"


def test_to_payload_sanitizes_serializer_failures(monkeypatch) -> None:
    """Encoding failures should not expose the serialized value."""

    def fail_serializer(_obj: object) -> object:
        raise ValueError("Bearer secret-token")

    monkeypatch.setattr("tracecat.dsl._converter._serializer", fail_serializer)

    converter = PydanticORJSONPayloadConverter()

    try:
        converter.to_payload(_LeakyValue())
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("Expected to_payload to raise RuntimeError")

    assert message == "Failed to encode payload value of type _LeakyValue"
    assert "secret-token" not in message


def test_from_payload_sanitizes_type_conversion_failures() -> None:
    """Decode/type conversion failures should not expose payload data."""
    converter = PydanticORJSONPayloadConverter()
    payload = Payload(
        metadata={"encoding": b"json/plain"},
        data=b'{"token":"Bearer secret-token"}',
    )

    try:
        converter.from_payload(payload, int)
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("Expected from_payload to raise RuntimeError")

    assert message == "Failed to decode payload for type int"
    assert "secret-token" not in message


def test_agent_action_memo_logging_omits_raw_payload(
    monkeypatch,
) -> None:
    """Memo parse warnings should not log the full protobuf payload."""
    warnings: list[dict[str, object]] = []

    def capture_warning(message: str, **kwargs: object) -> None:
        warnings.append({"message": message, **kwargs})

    def fail_from_payload(_payload: Payload) -> object:
        raise RuntimeError("decode failure")

    monkeypatch.setattr("tracecat.dsl.common.logger.warning", capture_warning)
    monkeypatch.setattr(
        "tracecat.dsl.common._memo_payload_converter.from_payload", fail_from_payload
    )

    memo = temporalio.api.common.v1.Memo(
        fields={
            "action_ref": Payload(
                metadata={"encoding": b"json/plain"},
                data=b'{"token":"Bearer secret-token"}',
            )
        }
    )

    AgentActionMemo.from_temporal(memo)

    assert len(warnings) == 1
    warning = warnings[0]
    assert warning["message"] == "Error parsing agent action memo field"
    assert warning["key"] == "action_ref"
    assert warning["encoding"] == "json/plain"
    assert warning["payload_size_bytes"] == len(b'{"token":"Bearer secret-token"}')
    assert "value" not in warning


def _build_tracecat_agent_config_payload() -> Payload:
    converter = PydanticORJSONPayloadConverter()
    payload = converter.to_payload(
        TracecatAgentConfig(
            model_name="gpt-5.2",
            model_provider="openai",
            instructions="You are a security analyst.",
            actions=["tools.datadog.change_signal_state"],
            namespaces=["tools.datadog"],
            tool_approvals={"tools.datadog.change_signal_state": True},
            mcp_servers=[
                {
                    "name": "internal-tools",
                    "url": "http://host.docker.internal:8080",
                    "transport": "http",
                    "headers": {"Authorization": "Bearer secret123"},
                }
            ],
            model_settings={"parallel_tool_calls": False},
            retries=3,
            enable_thinking=False,
            enable_internet_access=True,
        )
    )
    if payload is None:
        raise AssertionError("Expected JSON payload for AgentConfig")
    return payload


def test_converter_rejects_registry_agent_config_from_tracecat_agent_config_payload() -> (
    None
):
    """Current converter still fails on mixed AgentConfig types.

    The durable workflow fix avoids this by returning a workflow-safe payload
    across the activity boundary instead of AgentConfig directly.
    """
    converter = PydanticORJSONPayloadConverter()
    payload = _build_tracecat_agent_config_payload()

    with pytest.raises(
        RuntimeError,
        match="Failed to decode payload for type AgentConfig",
    ):
        converter.from_payload(payload, RegistryAgentConfig)


def test_converter_decodes_legacy_tracecat_agent_config_as_agent_config_payload() -> (
    None
):
    """Legacy AgentConfig payloads should decode for workflow replay compatibility."""
    converter = PydanticORJSONPayloadConverter()
    payload = _build_tracecat_agent_config_payload()

    decoded = converter.from_payload(payload, AgentConfigPayload)

    assert decoded.model_name == "gpt-5.2"
    assert decoded.model_provider == "openai"
    assert decoded.instructions == "You are a security analyst."
    assert decoded.actions == ["tools.datadog.change_signal_state"]
    assert decoded.namespaces == ["tools.datadog"]
    assert decoded.tool_approvals == {"tools.datadog.change_signal_state": True}
    assert decoded.model_settings == {"parallel_tool_calls": False}
    assert decoded.retries == 3
    assert decoded.enable_thinking is False
    assert decoded.enable_internet_access is True
    assert decoded.mcp_servers is not None
    assert len(decoded.mcp_servers) == 1
    server = decoded.mcp_servers[0]
    assert server.type == "http"
    assert server.name == "internal-tools"
    assert server.url == "http://host.docker.internal:8080"
    assert server.transport == "http"
    assert server.headers == {"Authorization": "Bearer secret123"}
