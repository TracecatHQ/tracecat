from __future__ import annotations

import base64
import time
import uuid

import pytest
from temporalio.api.common.v1 import Payload
from temporalio.converter import (
    DefaultFailureConverter,
    DefaultFailureConverterWithEncodedAttributes,
)

from tracecat import config
from tracecat.auth.types import Role
from tracecat.contexts import ctx_role
from tracecat.dsl._converter import get_data_converter
from tracecat.temporal import codec as temporal_codec
from tracecat.temporal.codec import (
    TRACECAT_TEMPORAL_ENCODING,
    TRACECAT_TEMPORAL_GLOBAL_SCOPE,
    CompositePayloadCodec,
    CompressionPayloadCodec,
    EncryptionPayloadCodec,
    TemporalEncryptionKeyring,
    TemporalPayloadCodecError,
    decode_payloads,
    get_payload_codec,
    reset_temporal_payload_secret_cache,
)

WORKSPACE_ID = uuid.uuid4()
WORKSPACE_ID_B = uuid.uuid4()


@pytest.fixture(autouse=True)
def reset_temporal_crypto_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "TEMPORAL__PAYLOAD_ENCRYPTION_ENABLED", False)
    monkeypatch.setattr(config, "TEMPORAL__PAYLOAD_ENCRYPTION_KEY", None)
    monkeypatch.setattr(config, "TEMPORAL__PAYLOAD_ENCRYPTION_KEY__ARN", None)
    monkeypatch.setattr(config, "TEMPORAL__PAYLOAD_ENCRYPTION_KEY_VERSION", "1")
    monkeypatch.setattr(config, "TEMPORAL__PAYLOAD_ENCRYPTION_CACHE_TTL_SECONDS", 3600)
    monkeypatch.setattr(config, "TEMPORAL__PAYLOAD_ENCRYPTION_CACHE_MAX_ITEMS", 128)
    monkeypatch.setattr(config, "TRACECAT__CONTEXT_COMPRESSION_ENABLED", False)
    monkeypatch.setattr(config, "TRACECAT__CONTEXT_COMPRESSION_ALGORITHM", "zstd")
    monkeypatch.setattr(config, "TRACECAT__CONTEXT_COMPRESSION_THRESHOLD_KB", 16)
    reset_temporal_payload_secret_cache()


def _enable_encryption(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "TEMPORAL__PAYLOAD_ENCRYPTION_ENABLED", True)
    monkeypatch.setattr(
        config, "TEMPORAL__PAYLOAD_ENCRYPTION_KEY", "unit-test-root-key"
    )


def _set_workspace_role(workspace_id: uuid.UUID = WORKSPACE_ID):
    """Set ctx_role with a workspace-scoped service role and return the token."""
    return ctx_role.set(
        Role(
            type="service",
            service_id="tracecat-service",
            workspace_id=workspace_id,
        )
    )


@pytest.mark.anyio
async def test_payload_codec_encrypts_and_decrypts_with_workspace_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_encryption(monkeypatch)

    codec = get_payload_codec(compression_enabled=False)
    assert codec is not None
    payload = Payload(
        metadata={"encoding": b"json/plain"},
        data=b'{"secret":"sensitive"}',
    )

    token = _set_workspace_role()
    try:
        encoded = await codec.encode([payload])
    finally:
        ctx_role.reset(token)

    encrypted = encoded[0]
    assert encrypted.metadata["encoding"] == b"binary/tracecat-aes256gcm"
    assert encrypted.metadata["tracecat_workspace_id"] == str(WORKSPACE_ID).encode()
    assert encrypted.data != payload.data

    decoded = await codec.decode(encoded)
    assert decoded[0].metadata["encoding"] == b"json/plain"
    assert decoded[0].data == payload.data


@pytest.mark.anyio
async def test_payload_codec_falls_back_to_global_scope_without_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When no workspace is set on the role, the codec uses the global scope."""
    _enable_encryption(monkeypatch)

    codec = get_payload_codec(compression_enabled=False)
    assert codec is not None

    # Role without workspace_id (platform-scoped)
    token = ctx_role.set(Role(type="service", service_id="tracecat-service"))
    try:
        encoded = await codec.encode(
            [Payload(metadata={"encoding": b"json/plain"}, data=b'{"scope":"global"}')]
        )
    finally:
        ctx_role.reset(token)

    encrypted = encoded[0]
    assert (
        encrypted.metadata["tracecat_workspace_id"]
        == TRACECAT_TEMPORAL_GLOBAL_SCOPE.encode()
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
    token = _set_workspace_role()
    try:
        encoded = await codec.encode([payload])
    finally:
        ctx_role.reset(token)

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


# --- EncryptionPayloadCodec edge cases ---


@pytest.mark.anyio
async def test_encryption_codec_passes_through_binary_null_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Payloads with encoding=binary/null are not encrypted."""
    _enable_encryption(monkeypatch)
    codec = EncryptionPayloadCodec(enabled=True)
    payload = Payload(metadata={"encoding": b"binary/null"}, data=b"\x00\x01\x02")

    token = _set_workspace_role()
    try:
        encoded = await codec.encode([payload])
    finally:
        ctx_role.reset(token)

    assert len(encoded) == 1
    assert encoded[0].metadata["encoding"] == b"binary/null"
    assert encoded[0].data == payload.data


@pytest.mark.anyio
async def test_encryption_codec_passes_through_empty_data_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Payloads with empty data are not encrypted."""
    _enable_encryption(monkeypatch)
    codec = EncryptionPayloadCodec(enabled=True)
    payload = Payload(metadata={"encoding": b"json/plain"}, data=b"")

    token = _set_workspace_role()
    try:
        encoded = await codec.encode([payload])
    finally:
        ctx_role.reset(token)

    assert len(encoded) == 1
    assert encoded[0].data == b""


@pytest.mark.anyio
async def test_encryption_codec_decode_passes_through_on_missing_workspace_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Decode passes through as-is when workspace ID is missing (fail-open)."""
    _enable_encryption(monkeypatch)
    codec = EncryptionPayloadCodec(enabled=True)

    bad_payload = Payload(
        metadata={
            "encoding": TRACECAT_TEMPORAL_ENCODING,
            # tracecat_workspace_id intentionally omitted
            "tracecat_nonce": base64.urlsafe_b64encode(b"012345678901"),
        },
        data=b"ciphertext",
    )

    result = await codec.decode([bad_payload])
    assert len(result) == 1
    assert result[0].data == b"ciphertext"


@pytest.mark.anyio
async def test_encryption_codec_decode_passes_through_on_missing_nonce(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Decode passes through as-is when nonce is missing (fail-open)."""
    _enable_encryption(monkeypatch)
    codec = EncryptionPayloadCodec(enabled=True)

    bad_payload = Payload(
        metadata={
            "encoding": TRACECAT_TEMPORAL_ENCODING,
            "tracecat_workspace_id": str(WORKSPACE_ID).encode(),
            # tracecat_nonce intentionally omitted
        },
        data=b"ciphertext",
    )

    result = await codec.decode([bad_payload])
    assert len(result) == 1
    assert result[0].data == b"ciphertext"


@pytest.mark.anyio
async def test_encryption_codec_decode_passes_through_on_tampered_ciphertext(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Decode passes through as-is when ciphertext is tampered with (fail-open)."""
    _enable_encryption(monkeypatch)
    codec = EncryptionPayloadCodec(enabled=True)

    token = _set_workspace_role()
    try:
        encoded = await codec.encode(
            [Payload(metadata={"encoding": b"json/plain"}, data=b'{"ok":true}')]
        )
    finally:
        ctx_role.reset(token)

    tampered = Payload(
        metadata=dict(encoded[0].metadata),
        data=b"\xff" * len(encoded[0].data),
    )

    result = await codec.decode([tampered])
    assert len(result) == 1
    assert result[0].data == tampered.data


@pytest.mark.anyio
async def test_encryption_codec_cross_workspace_decode_passes_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Payload encrypted under workspace A passes through on workspace B decode (fail-open)."""
    _enable_encryption(monkeypatch)
    codec = EncryptionPayloadCodec(enabled=True)
    payload = Payload(metadata={"encoding": b"json/plain"}, data=b'{"ws":"a"}')

    # Encrypt under workspace A
    token_a = _set_workspace_role(WORKSPACE_ID)
    try:
        encoded = await codec.encode([payload])
    finally:
        ctx_role.reset(token_a)

    # Swap workspace ID in metadata to workspace B
    tampered_metadata = dict(encoded[0].metadata)
    tampered_metadata["tracecat_workspace_id"] = str(WORKSPACE_ID_B).encode()
    wrong_ws_payload = Payload(metadata=tampered_metadata, data=encoded[0].data)

    result = await codec.decode([wrong_ws_payload])
    assert len(result) == 1
    assert result[0].data == wrong_ws_payload.data


# --- Encode fail-open tests ---


@pytest.mark.anyio
async def test_encryption_codec_encode_passes_through_on_key_init_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Encode passes through unencrypted when key initialization fails (fail-open)."""
    monkeypatch.setattr(config, "TEMPORAL__PAYLOAD_ENCRYPTION_ENABLED", True)
    monkeypatch.setattr(config, "TEMPORAL__PAYLOAD_ENCRYPTION_KEY", None)
    monkeypatch.setattr(config, "TEMPORAL__PAYLOAD_ENCRYPTION_KEY__ARN", None)

    codec = EncryptionPayloadCodec(enabled=True)
    payload = Payload(metadata={"encoding": b"json/plain"}, data=b'{"ok":true}')

    token = _set_workspace_role()
    try:
        result = await codec.encode([payload])
    finally:
        ctx_role.reset(token)

    assert len(result) == 1
    assert result[0].data == payload.data
    assert result[0].metadata["encoding"] == b"json/plain"


# --- Keyring tests ---


@pytest.mark.anyio
async def test_keyring_raises_when_no_key_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keyring raises when neither env-key nor ARN is configured."""
    monkeypatch.setattr(config, "TEMPORAL__PAYLOAD_ENCRYPTION_KEY", None)
    monkeypatch.setattr(config, "TEMPORAL__PAYLOAD_ENCRYPTION_KEY__ARN", None)

    keyring = TemporalEncryptionKeyring()
    with pytest.raises(TemporalPayloadCodecError, match="no root key is configured"):
        await keyring.get_key("workspace-1", "1")


@pytest.mark.anyio
async def test_keyring_cache_evicts_oldest_when_max_items_exceeded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cache evicts the oldest entry when max_items is exceeded."""
    monkeypatch.setattr(
        config, "TEMPORAL__PAYLOAD_ENCRYPTION_KEY", "unit-test-root-key"
    )
    monkeypatch.setattr(config, "TEMPORAL__PAYLOAD_ENCRYPTION_CACHE_MAX_ITEMS", 2)
    monkeypatch.setattr(config, "TEMPORAL__PAYLOAD_ENCRYPTION_CACHE_TTL_SECONDS", 3600)

    keyring = TemporalEncryptionKeyring()
    key1 = await keyring.get_key("ws-1", "1")
    await keyring.get_key("ws-2", "1")
    await keyring.get_key("ws-3", "1")  # Should evict ws-1

    # ws-1 was evicted so it's re-derived — should still produce same key
    key1_again = await keyring.get_key("ws-1", "1")
    assert key1 == key1_again
    # Cache now holds ws-3 and ws-1; ws-2 was evicted
    assert len(keyring._cache) == 2


@pytest.mark.anyio
async def test_keyring_cache_expires_stale_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cache entries are re-derived after TTL expires."""
    monkeypatch.setattr(
        config, "TEMPORAL__PAYLOAD_ENCRYPTION_KEY", "unit-test-root-key"
    )
    monkeypatch.setattr(config, "TEMPORAL__PAYLOAD_ENCRYPTION_CACHE_TTL_SECONDS", 0)

    keyring = TemporalEncryptionKeyring()
    await keyring.get_key("ws-1", "1")

    # With TTL=0, the entry is immediately stale
    cache_key = ("ws-1", "1")
    assert cache_key in keyring._cache
    # Force the expiry time into the past
    key_bytes, _ = keyring._cache[cache_key]
    keyring._cache[cache_key] = (key_bytes, time.monotonic() - 1)

    # Should re-derive (and produce the same key)
    key_again = await keyring.get_key("ws-1", "1")
    assert key_again == key_bytes


# --- CompressionPayloadCodec tests ---


@pytest.mark.anyio
async def test_compression_codec_roundtrip() -> None:
    """Compression codec compresses large payloads and decompresses them."""
    codec = CompressionPayloadCodec(threshold_bytes=10, algorithm="zstd", enabled=True)
    large_data = b"a]" * 100  # Well above the 10-byte threshold
    payload = Payload(metadata={"encoding": b"json/plain"}, data=large_data)

    encoded = await codec.encode([payload])
    assert encoded[0].metadata["encoding"] == b"binary/zstd"
    assert encoded[0].data != large_data

    decoded = await codec.decode(encoded)
    assert decoded[0].data == large_data
    assert decoded[0].metadata.get("encoding") == b"json/plain"


@pytest.mark.anyio
async def test_compression_codec_skips_small_payloads() -> None:
    """Payloads below the threshold are not compressed."""
    codec = CompressionPayloadCodec(
        threshold_bytes=1024, algorithm="zstd", enabled=True
    )
    small_data = b"tiny"
    payload = Payload(metadata={"encoding": b"json/plain"}, data=small_data)

    encoded = await codec.encode([payload])
    assert encoded[0].metadata["encoding"] == b"json/plain"
    assert encoded[0].data == small_data


@pytest.mark.anyio
async def test_compression_codec_disabled_is_passthrough() -> None:
    """When disabled, compression codec passes payloads through unchanged."""
    codec = CompressionPayloadCodec(threshold_bytes=1, algorithm="zstd", enabled=False)
    data = b"a" * 100
    payload = Payload(metadata={"encoding": b"json/plain"}, data=data)

    encoded = await codec.encode([payload])
    assert encoded[0].data == data


# --- CompositePayloadCodec / get_payload_codec factory tests ---


def test_get_payload_codec_always_returns_composite() -> None:
    """get_payload_codec always includes both codecs for decode compatibility."""
    codec = get_payload_codec(compression_enabled=False)
    assert isinstance(codec, CompositePayloadCodec)
    assert len(codec.codecs) == 2
    assert isinstance(codec.codecs[0], CompressionPayloadCodec)
    assert isinstance(codec.codecs[1], EncryptionPayloadCodec)


def test_get_payload_codec_compression_flag_controls_encode_only() -> None:
    """compression_enabled=False disables encode but codec is still present for decode."""
    codec = get_payload_codec(compression_enabled=False)
    assert isinstance(codec, CompositePayloadCodec)
    assert not codec.codecs[0].enabled  # type: ignore[union-attr]

    codec_on = get_payload_codec(compression_enabled=True)
    assert isinstance(codec_on, CompositePayloadCodec)
    assert codec_on.codecs[0].enabled  # type: ignore[union-attr]


@pytest.mark.anyio
async def test_composite_codec_compress_then_encrypt_roundtrip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full pipeline: compress → encrypt → decrypt → decompress."""
    _enable_encryption(monkeypatch)
    monkeypatch.setattr(config, "TRACECAT__CONTEXT_COMPRESSION_ENABLED", True)

    codec = get_payload_codec(compression_enabled=True)
    assert codec is not None
    large_data = b'{"key":"' + b"x" * 200 + b'"}'
    payload = Payload(metadata={"encoding": b"json/plain"}, data=large_data)

    token = _set_workspace_role()
    try:
        encoded = await codec.encode([payload])
    finally:
        ctx_role.reset(token)

    # Outermost encoding should be encryption
    assert encoded[0].metadata["encoding"] == TRACECAT_TEMPORAL_ENCODING

    decoded = await codec.decode(encoded)
    assert decoded[0].data == large_data
    assert decoded[0].metadata.get("encoding") == b"json/plain"


# --- Decode-path regression tests (issues #4 and #5) ---


@pytest.mark.anyio
async def test_decode_payloads_decrypts_when_encryption_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Historical encrypted payloads are decoded even when encryption is disabled."""
    _enable_encryption(monkeypatch)
    codec = EncryptionPayloadCodec(enabled=True)
    payload = Payload(metadata={"encoding": b"json/plain"}, data=b'{"secret":"data"}')

    token = _set_workspace_role()
    try:
        encrypted = await codec.encode([payload])
    finally:
        ctx_role.reset(token)

    # Disable encryption — simulates config change / rollback
    monkeypatch.setattr(config, "TEMPORAL__PAYLOAD_ENCRYPTION_ENABLED", False)

    result = await decode_payloads(encrypted)
    assert result[0].data == payload.data
    assert result[0].metadata["encoding"] == b"json/plain"


@pytest.mark.anyio
async def test_decode_payloads_decompresses_when_compression_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Historical compressed payloads are decoded even when compression is disabled."""
    compress_codec = CompressionPayloadCodec(
        threshold_bytes=1, algorithm="zstd", enabled=True
    )
    payload = Payload(metadata={"encoding": b"json/plain"}, data=b'{"key":"value"}')
    compressed = await compress_codec.encode([payload])
    assert compressed[0].metadata["encoding"] == b"binary/zstd"

    # Disable compression — simulates config change
    monkeypatch.setattr(config, "TRACECAT__CONTEXT_COMPRESSION_ENABLED", False)

    result = await decode_payloads(compressed)
    assert result[0].data == payload.data
    assert result[0].metadata.get("encoding") == b"json/plain"


@pytest.mark.anyio
async def test_decode_payloads_handles_compressed_then_encrypted_when_both_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Historical compress+encrypt payloads are decoded when both flags are off."""
    _enable_encryption(monkeypatch)
    codec = get_payload_codec(compression_enabled=True)
    large_data = b'{"key":"' + b"x" * 200 + b'"}'
    payload = Payload(metadata={"encoding": b"json/plain"}, data=large_data)

    token = _set_workspace_role()
    try:
        encoded = await codec.encode([payload])
    finally:
        ctx_role.reset(token)

    # Disable both — simulates full rollback
    monkeypatch.setattr(config, "TEMPORAL__PAYLOAD_ENCRYPTION_ENABLED", False)
    monkeypatch.setattr(config, "TRACECAT__CONTEXT_COMPRESSION_ENABLED", False)

    result = await decode_payloads(encoded)
    assert result[0].data == large_data
    assert result[0].metadata.get("encoding") == b"json/plain"
