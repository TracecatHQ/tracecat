from __future__ import annotations

import base64

import pytest
from temporalio.api.common.v1 import Payload
from temporalio.converter import (
    DefaultFailureConverter,
    DefaultFailureConverterWithEncodedAttributes,
)

from tracecat import config
from tracecat.contexts import with_temporal_workspace_id
from tracecat.dsl._converter import get_data_converter
from tracecat.temporal import codec as temporal_codec
from tracecat.temporal.codec import (
    TemporalPayloadCodecError,
    get_payload_codec,
    reset_temporal_payload_secret_cache,
)


@pytest.fixture(autouse=True)
def reset_temporal_crypto_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "TEMPORAL__PAYLOAD_ENCRYPTION_ENABLED", False)
    monkeypatch.setattr(config, "TEMPORAL__PAYLOAD_ENCRYPTION_KEY", None)
    monkeypatch.setattr(config, "TEMPORAL__PAYLOAD_ENCRYPTION_KEY__ARN", None)
    monkeypatch.setattr(config, "TEMPORAL__PAYLOAD_ENCRYPTION_KEY_VERSION", "1")
    monkeypatch.setattr(config, "TEMPORAL__PAYLOAD_ENCRYPTION_CACHE_TTL_SECONDS", 3600)
    monkeypatch.setattr(config, "TEMPORAL__PAYLOAD_ENCRYPTION_CACHE_MAX_ITEMS", 128)
    reset_temporal_payload_secret_cache()


@pytest.mark.anyio
async def test_payload_codec_encrypts_and_decrypts_with_workspace_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TEMPORAL__PAYLOAD_ENCRYPTION_ENABLED", True)
    monkeypatch.setattr(
        config, "TEMPORAL__PAYLOAD_ENCRYPTION_KEY", "unit-test-root-key"
    )

    codec = get_payload_codec(compression_enabled=False)
    assert codec is not None
    payload = Payload(
        metadata={"encoding": b"json/plain"},
        data=b'{"secret":"sensitive"}',
    )

    with with_temporal_workspace_id("workspace-123"):
        encoded = await codec.encode([payload])

    encrypted = encoded[0]
    assert encrypted.metadata["encoding"] == b"binary/tracecat-aes256gcm"
    assert encrypted.metadata["tracecat_workspace_id"] == b"workspace-123"
    assert encrypted.data != payload.data

    decoded = await codec.decode(encoded)
    assert decoded[0].metadata["encoding"] == b"json/plain"
    assert decoded[0].data == payload.data


@pytest.mark.anyio
async def test_payload_codec_requires_explicit_workspace_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TEMPORAL__PAYLOAD_ENCRYPTION_ENABLED", True)
    monkeypatch.setattr(
        config, "TEMPORAL__PAYLOAD_ENCRYPTION_KEY", "unit-test-root-key"
    )

    codec = get_payload_codec(compression_enabled=False)
    assert codec is not None
    with pytest.raises(
        TemporalPayloadCodecError, match="requires an explicit workspace scope"
    ):
        await codec.encode(
            [Payload(metadata={"encoding": b"json/plain"}, data=b'{"scope":"global"}')]
        )


@pytest.mark.anyio
async def test_payload_codec_retrieves_root_key_from_secret_arn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TEMPORAL__PAYLOAD_ENCRYPTION_ENABLED", True)
    monkeypatch.setattr(config, "TEMPORAL__PAYLOAD_ENCRYPTION_KEY", None)
    monkeypatch.setattr(
        config,
        "TEMPORAL__PAYLOAD_ENCRYPTION_KEY__ARN",
        "arn:aws:secretsmanager:us-east-1:123456789012:secret:tracecat",
    )

    calls = 0

    class SecretsManagerClient:
        def get_secret_value(self, *, SecretId: str) -> dict[str, bytes]:
            nonlocal calls
            calls += 1
            assert SecretId == config.TEMPORAL__PAYLOAD_ENCRYPTION_KEY__ARN
            return {"SecretBinary": base64.b64encode(b"unit-test-root-key")}

    class Session:
        def client(self, *, service_name: str) -> SecretsManagerClient:
            assert service_name == "secretsmanager"
            return SecretsManagerClient()

    monkeypatch.setattr(temporal_codec.boto3.session, "Session", lambda: Session())

    codec = get_payload_codec(compression_enabled=False)
    assert codec is not None

    payload = Payload(
        metadata={"encoding": b"json/plain"},
        data=b'{"secret":"sensitive"}',
    )
    with with_temporal_workspace_id("workspace-123"):
        encoded = await codec.encode([payload])

    decoded = await codec.decode(encoded)
    assert decoded[0].data == payload.data
    assert calls == 1


def test_get_data_converter_switches_failure_converter_with_encryption(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TEMPORAL__PAYLOAD_ENCRYPTION_ENABLED", False)
    converter = get_data_converter(compression_enabled=False)
    assert isinstance(converter.failure_converter, DefaultFailureConverter)

    monkeypatch.setattr(config, "TEMPORAL__PAYLOAD_ENCRYPTION_ENABLED", True)
    encrypted_converter = get_data_converter(compression_enabled=False)
    assert isinstance(
        encrypted_converter.failure_converter,
        DefaultFailureConverterWithEncodedAttributes,
    )
