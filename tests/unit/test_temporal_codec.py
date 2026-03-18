from __future__ import annotations

import pytest
from temporalio.api.common.v1 import Payload
from temporalio.converter import (
    DefaultFailureConverter,
    DefaultFailureConverterWithEncodedAttributes,
)

from tracecat import config
from tracecat.contexts import with_temporal_workspace_id
from tracecat.dsl._converter import get_data_converter
from tracecat.temporal.codec import (
    TRACECAT_TEMPORAL_GLOBAL_SCOPE,
    get_payload_codec,
    reset_temporal_payload_secret_cache,
)
from tracecat.temporal.visibility import (
    reset_temporal_visibility_secret_cache,
    tokenize_visibility_value,
)


@pytest.fixture(autouse=True)
def reset_temporal_crypto_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "TEMPORAL__PAYLOAD_ENCRYPTION_ENABLED", False)
    monkeypatch.setattr(config, "TEMPORAL__PAYLOAD_ENCRYPTION_KEY", None)
    monkeypatch.setattr(config, "TEMPORAL__PAYLOAD_ENCRYPTION_KEY__ARN", None)
    monkeypatch.setattr(config, "TEMPORAL__PAYLOAD_ENCRYPTION_KEY_VERSION", "1")
    monkeypatch.setattr(config, "TEMPORAL__PAYLOAD_ENCRYPTION_CACHE_TTL_SECONDS", 3600)
    monkeypatch.setattr(config, "TEMPORAL__PAYLOAD_ENCRYPTION_CACHE_MAX_ITEMS", 128)
    monkeypatch.setattr(config, "TEMPORAL__VISIBILITY_HMAC_KEY", None)
    monkeypatch.setattr(config, "TEMPORAL__VISIBILITY_HMAC_KEY__ARN", None)
    reset_temporal_payload_secret_cache()
    reset_temporal_visibility_secret_cache()


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
async def test_payload_codec_falls_back_to_global_scope_without_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TEMPORAL__PAYLOAD_ENCRYPTION_ENABLED", True)
    monkeypatch.setattr(
        config, "TEMPORAL__PAYLOAD_ENCRYPTION_KEY", "unit-test-root-key"
    )

    codec = get_payload_codec(compression_enabled=False)
    assert codec is not None
    encoded = await codec.encode(
        [Payload(metadata={"encoding": b"json/plain"}, data=b'{"scope":"global"}')]
    )

    assert (
        encoded[0].metadata["tracecat_workspace_id"].decode("utf-8")
        == TRACECAT_TEMPORAL_GLOBAL_SCOPE
    )


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


def test_tokenize_visibility_value_is_deterministic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TEMPORAL__VISIBILITY_HMAC_KEY", "visibility-hmac-key")

    token_a = tokenize_visibility_value("workflow-alias")
    token_b = tokenize_visibility_value("workflow-alias")
    token_c = tokenize_visibility_value("other-alias")

    assert token_a == token_b
    assert token_a != token_c
    assert token_a.startswith("h1:")
