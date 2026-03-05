"""Tests for tracecat.dsl._converter payload sanitization."""

from __future__ import annotations

import temporalio.api.common.v1
from temporalio.api.common.v1 import Payload

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
