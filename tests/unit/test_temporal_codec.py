from __future__ import annotations

import base64
import uuid

import orjson
import pytest
from botocore.exceptions import EndpointConnectionError
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
    TemporalPayloadKeyring,
    decode_payloads,
    get_payload_codec,
    reset_temporal_payload_codec_cache,
    reset_temporal_payload_secret_cache,
)

WORKSPACE_ID = uuid.uuid4()
WORKSPACE_ID_B = uuid.uuid4()


@pytest.fixture(autouse=True)
def reset_temporal_crypto_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "TEMPORAL__PAYLOAD_ENCRYPTION_ENABLED", False)
    monkeypatch.setattr(config, "TEMPORAL__PAYLOAD_ENCRYPTION_KEYRING", None)
    monkeypatch.setattr(config, "TEMPORAL__PAYLOAD_ENCRYPTION_KEYRING_ARN", None)
    monkeypatch.setattr(config, "TEMPORAL__PAYLOAD_ENCRYPTION_CACHE_TTL_SECONDS", 3600)
    monkeypatch.setattr(config, "TEMPORAL__PAYLOAD_ENCRYPTION_CACHE_MAX_ITEMS", 128)
    monkeypatch.setattr(config, "TRACECAT__CONTEXT_COMPRESSION_ENABLED", False)
    monkeypatch.setattr(config, "TRACECAT__CONTEXT_COMPRESSION_ALGORITHM", "zstd")
    monkeypatch.setattr(config, "TRACECAT__CONTEXT_COMPRESSION_THRESHOLD_KB", 16)
    reset_temporal_payload_codec_cache()
    reset_temporal_payload_secret_cache()


def _keyring_json(
    *,
    current_key_id: str = "v1",
    keys: dict[str, str] | None = None,
) -> str:
    return orjson.dumps(
        {
            "current_key_id": current_key_id,
            "keys": keys or {"v1": "unit-test-root-key"},
        }
    ).decode()


def _enable_encryption(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "TEMPORAL__PAYLOAD_ENCRYPTION_ENABLED", True)
    monkeypatch.setattr(config, "TEMPORAL__PAYLOAD_ENCRYPTION_KEYRING", _keyring_json())


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
    assert encrypted.metadata["tracecat_key_id"] == b"v1"
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
    monkeypatch.setattr(config, "TEMPORAL__PAYLOAD_ENCRYPTION_KEYRING", None)
    monkeypatch.setattr(
        config,
        "TEMPORAL__PAYLOAD_ENCRYPTION_KEYRING_ARN",
        "arn:aws:secretsmanager:us-east-1:123456789012:secret:tracecat",
    )
    keyring_secret = _keyring_json()

    calls = 0

    class SecretsManagerClient:
        def get_secret_value(self, *, SecretId: str) -> dict[str, bytes]:
            nonlocal calls
            calls += 1
            assert SecretId == config.TEMPORAL__PAYLOAD_ENCRYPTION_KEYRING_ARN
            return {"SecretBinary": keyring_secret.encode()}

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


@pytest.mark.anyio
async def test_keyring_wraps_malformed_binary_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TEMPORAL__PAYLOAD_ENCRYPTION_KEYRING", None)
    monkeypatch.setattr(
        config,
        "TEMPORAL__PAYLOAD_ENCRYPTION_KEYRING_ARN",
        "arn:aws:secretsmanager:us-east-1:123456789012:secret:tracecat",
    )

    class SecretsManagerClient:
        def get_secret_value(self, *, SecretId: str) -> dict[str, bytes]:
            assert SecretId == config.TEMPORAL__PAYLOAD_ENCRYPTION_KEYRING_ARN
            return {"SecretBinary": b"\xff\xfe"}

    class Session:
        def client(self, *, service_name: str) -> SecretsManagerClient:
            assert service_name == "secretsmanager"
            return SecretsManagerClient()

    monkeypatch.setattr(temporal_codec.boto3.session, "Session", lambda: Session())

    keyring = TemporalEncryptionKeyring()
    with pytest.raises(
        TemporalPayloadCodecError,
        match="Temporal payload encryption keyring SecretBinary is invalid",
    ):
        await keyring.get_current_key_id()


@pytest.mark.anyio
async def test_keyring_wraps_aws_secret_retrieval_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TEMPORAL__PAYLOAD_ENCRYPTION_KEYRING", None)
    monkeypatch.setattr(
        config,
        "TEMPORAL__PAYLOAD_ENCRYPTION_KEYRING_ARN",
        "arn:aws:secretsmanager:us-east-1:123456789012:secret:tracecat",
    )

    class SecretsManagerClient:
        def get_secret_value(self, *, SecretId: str) -> dict[str, str]:
            assert SecretId == config.TEMPORAL__PAYLOAD_ENCRYPTION_KEYRING_ARN
            raise EndpointConnectionError(endpoint_url="https://secretsmanager")

    class Session:
        def client(self, *, service_name: str) -> SecretsManagerClient:
            assert service_name == "secretsmanager"
            return SecretsManagerClient()

    monkeypatch.setattr(temporal_codec.boto3.session, "Session", lambda: Session())

    keyring = TemporalEncryptionKeyring()
    with pytest.raises(
        TemporalPayloadCodecError,
        match="Failed to retrieve Temporal payload encryption keyring",
    ):
        await keyring.get_current_key_id()


@pytest.mark.anyio
async def test_keyring_uses_cached_secret_when_refresh_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TEMPORAL__PAYLOAD_ENCRYPTION_KEYRING", None)
    monkeypatch.setattr(
        config,
        "TEMPORAL__PAYLOAD_ENCRYPTION_KEYRING_ARN",
        "arn:aws:secretsmanager:us-east-1:123456789012:secret:tracecat",
    )
    monkeypatch.setattr(config, "TEMPORAL__PAYLOAD_ENCRYPTION_CACHE_TTL_SECONDS", 0)

    calls = 0

    class SecretsManagerClient:
        def get_secret_value(self, *, SecretId: str) -> dict[str, str]:
            nonlocal calls
            calls += 1
            assert SecretId == config.TEMPORAL__PAYLOAD_ENCRYPTION_KEYRING_ARN
            if calls == 1:
                return {"SecretString": _keyring_json()}
            raise EndpointConnectionError(endpoint_url="https://secretsmanager")

    class Session:
        def client(self, *, service_name: str) -> SecretsManagerClient:
            assert service_name == "secretsmanager"
            return SecretsManagerClient()

    monkeypatch.setattr(temporal_codec.boto3.session, "Session", lambda: Session())

    keyring = TemporalEncryptionKeyring()
    assert await keyring.get_current_key_id() == "v1"
    assert await keyring.get_current_key_id() == "v1"
    assert await keyring.get_current_key_id() == "v1"
    assert calls == 2


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
async def test_encryption_codec_decode_fails_on_missing_workspace_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tracecat-encrypted payloads fail closed when workspace ID is missing."""
    _enable_encryption(monkeypatch)
    codec = EncryptionPayloadCodec(enabled=True)

    bad_payload = Payload(
        metadata={
            "encoding": TRACECAT_TEMPORAL_ENCODING,
            # tracecat_workspace_id intentionally omitted
            "tracecat_key_id": b"v1",
            "tracecat_nonce": base64.urlsafe_b64encode(b"012345678901"),
        },
        data=b"ciphertext",
    )

    with pytest.raises(
        TemporalPayloadCodecError, match="Failed to decrypt Temporal payload"
    ):
        await codec.decode([bad_payload])


@pytest.mark.anyio
async def test_encryption_codec_decode_fails_on_missing_key_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tracecat-encrypted payloads fail closed when key id is missing."""
    _enable_encryption(monkeypatch)
    codec = EncryptionPayloadCodec(enabled=True)

    bad_payload = Payload(
        metadata={
            "encoding": TRACECAT_TEMPORAL_ENCODING,
            "tracecat_workspace_id": str(WORKSPACE_ID).encode(),
            # tracecat_key_id intentionally omitted
            "tracecat_nonce": base64.urlsafe_b64encode(b"012345678901"),
        },
        data=b"ciphertext",
    )

    with pytest.raises(
        TemporalPayloadCodecError, match="Failed to decrypt Temporal payload"
    ):
        await codec.decode([bad_payload])


@pytest.mark.anyio
async def test_encryption_codec_decode_fails_on_missing_nonce(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tracecat-encrypted payloads fail closed when nonce is missing."""
    _enable_encryption(monkeypatch)
    codec = EncryptionPayloadCodec(enabled=True)

    bad_payload = Payload(
        metadata={
            "encoding": TRACECAT_TEMPORAL_ENCODING,
            "tracecat_workspace_id": str(WORKSPACE_ID).encode(),
            "tracecat_key_id": b"v1",
            # tracecat_nonce intentionally omitted
        },
        data=b"ciphertext",
    )

    with pytest.raises(
        TemporalPayloadCodecError, match="Failed to decrypt Temporal payload"
    ):
        await codec.decode([bad_payload])


@pytest.mark.anyio
async def test_encryption_codec_decode_fails_on_unknown_key_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tracecat-encrypted payloads fail closed when key id is unknown."""
    _enable_encryption(monkeypatch)
    codec = EncryptionPayloadCodec(enabled=True)

    token = _set_workspace_role()
    try:
        encoded = await codec.encode(
            [Payload(metadata={"encoding": b"json/plain"}, data=b'{"ok":true}')]
        )
    finally:
        ctx_role.reset(token)

    unknown_key_metadata = dict(encoded[0].metadata)
    unknown_key_metadata["tracecat_key_id"] = b"unknown"
    unknown_key_payload = Payload(
        metadata=unknown_key_metadata,
        data=encoded[0].data,
    )

    with pytest.raises(
        TemporalPayloadCodecError, match="Failed to decrypt Temporal payload"
    ):
        await codec.decode([unknown_key_payload])


@pytest.mark.anyio
async def test_encryption_codec_decode_fails_on_tampered_ciphertext(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tracecat-encrypted payloads fail closed when ciphertext is tampered with."""
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

    with pytest.raises(
        TemporalPayloadCodecError, match="Failed to decrypt Temporal payload"
    ):
        await codec.decode([tampered])


@pytest.mark.anyio
async def test_encryption_codec_decode_fails_on_tampered_original_encoding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tracecat-encrypted payloads fail closed when original encoding is tampered."""
    _enable_encryption(monkeypatch)
    codec = EncryptionPayloadCodec(enabled=True)

    token = _set_workspace_role()
    try:
        encoded = await codec.encode(
            [Payload(metadata={"encoding": b"json/plain"}, data=b'{"ok":true}')]
        )
    finally:
        ctx_role.reset(token)

    tampered_metadata = dict(encoded[0].metadata)
    tampered_metadata["tracecat_original_encoding"] = b"binary/null"
    tampered = Payload(metadata=tampered_metadata, data=encoded[0].data)

    with pytest.raises(
        TemporalPayloadCodecError, match="Failed to decrypt Temporal payload"
    ):
        await codec.decode([tampered])


@pytest.mark.anyio
async def test_encryption_codec_decode_fails_on_tampered_encoding_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tracecat-encrypted payloads fail closed when encoding marker is tampered."""
    _enable_encryption(monkeypatch)
    codec = EncryptionPayloadCodec(enabled=True)

    token = _set_workspace_role()
    try:
        encoded = await codec.encode(
            [Payload(metadata={"encoding": b"json/plain"}, data=b'{"ok":true}')]
        )
    finally:
        ctx_role.reset(token)

    tampered_metadata = dict(encoded[0].metadata)
    tampered_metadata["encoding"] = b"binary/null"
    tampered = Payload(metadata=tampered_metadata, data=encoded[0].data)

    with pytest.raises(
        TemporalPayloadCodecError,
        match="Encrypted Temporal payload has an invalid encoding marker",
    ):
        await codec.decode([tampered])


@pytest.mark.anyio
async def test_encryption_codec_cross_workspace_decode_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tracecat-encrypted payloads fail closed when workspace metadata is tampered."""
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

    with pytest.raises(
        TemporalPayloadCodecError, match="Failed to decrypt Temporal payload"
    ):
        await codec.decode([wrong_ws_payload])


# --- Encode fail-closed tests ---


@pytest.mark.anyio
async def test_encryption_codec_encode_fails_on_key_init_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Encode fails closed when encryption is enabled without a keyring."""
    monkeypatch.setattr(config, "TEMPORAL__PAYLOAD_ENCRYPTION_ENABLED", True)
    monkeypatch.setattr(config, "TEMPORAL__PAYLOAD_ENCRYPTION_KEYRING", None)
    monkeypatch.setattr(config, "TEMPORAL__PAYLOAD_ENCRYPTION_KEYRING_ARN", None)

    codec = EncryptionPayloadCodec(enabled=True)
    payload = Payload(metadata={"encoding": b"json/plain"}, data=b'{"ok":true}')

    token = _set_workspace_role()
    try:
        with pytest.raises(TemporalPayloadCodecError, match="no keyring is configured"):
            await codec.encode([payload])
    finally:
        ctx_role.reset(token)


# --- Keyring tests ---


@pytest.mark.anyio
async def test_keyring_raises_when_no_key_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keyring raises when neither env-keyring nor ARN is configured."""
    monkeypatch.setattr(config, "TEMPORAL__PAYLOAD_ENCRYPTION_KEYRING", None)
    monkeypatch.setattr(config, "TEMPORAL__PAYLOAD_ENCRYPTION_KEYRING_ARN", None)

    keyring = TemporalEncryptionKeyring()
    with pytest.raises(TemporalPayloadCodecError, match="no keyring is configured"):
        await keyring.get_key("workspace-1", "v1")


@pytest.mark.anyio
@pytest.mark.parametrize(
    "keyring_json",
    [
        "not-json",
        "{}",
        _keyring_json(current_key_id="", keys={"v1": "unit-test-root-key"}),
        _keyring_json(current_key_id="v2", keys={"v1": "unit-test-root-key"}),
        _keyring_json(current_key_id="v1", keys={"v1": ""}),
    ],
)
async def test_keyring_raises_on_invalid_keyring_config(
    monkeypatch: pytest.MonkeyPatch,
    keyring_json: str,
) -> None:
    """Keyring validates malformed keyring configuration."""
    monkeypatch.setattr(config, "TEMPORAL__PAYLOAD_ENCRYPTION_KEYRING", keyring_json)

    keyring = TemporalEncryptionKeyring()
    with pytest.raises(TemporalPayloadCodecError, match="keyring is invalid"):
        await keyring.get_current_key_id()


@pytest.mark.anyio
async def test_keyring_cache_evicts_oldest_when_max_items_exceeded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cache evicts the oldest entry when max_items is exceeded."""
    monkeypatch.setattr(config, "TEMPORAL__PAYLOAD_ENCRYPTION_KEYRING", _keyring_json())
    monkeypatch.setattr(config, "TEMPORAL__PAYLOAD_ENCRYPTION_CACHE_MAX_ITEMS", 2)
    monkeypatch.setattr(config, "TEMPORAL__PAYLOAD_ENCRYPTION_CACHE_TTL_SECONDS", 3600)

    keyring = TemporalEncryptionKeyring()
    key1 = await keyring.get_key("ws-1", "v1")
    await keyring.get_key("ws-2", "v1")
    await keyring.get_key("ws-3", "v1")  # Should evict ws-1

    # ws-1 was evicted so it's re-derived — should still produce same key
    key1_again = await keyring.get_key("ws-1", "v1")
    assert key1 == key1_again
    # Cache now holds ws-3 and ws-1; ws-2 was evicted
    assert len(keyring._cache) == 2


@pytest.mark.anyio
async def test_keyring_cache_expires_stale_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cache entries are re-derived after TTL expires."""
    monkeypatch.setattr(config, "TEMPORAL__PAYLOAD_ENCRYPTION_KEYRING", _keyring_json())
    monkeypatch.setattr(config, "TEMPORAL__PAYLOAD_ENCRYPTION_CACHE_TTL_SECONDS", 0)

    keyring = TemporalEncryptionKeyring()
    key_1 = await keyring.get_key("ws-1", "v1")

    # With TTL=0, TTLCache expires entries immediately — cache should be empty
    cache_key = ("ws-1", "v1")
    assert cache_key not in keyring._cache

    # Should re-derive (and produce the same deterministic key)
    key_again = await keyring.get_key("ws-1", "v1")
    assert key_again == key_1


@pytest.mark.anyio
async def test_keyring_secret_cache_refreshes_after_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keyring secret cache reloads rotated keyring configuration after TTL expiry."""
    monkeypatch.setattr(
        config,
        "TEMPORAL__PAYLOAD_ENCRYPTION_KEYRING",
        _keyring_json(
            current_key_id="v1",
            keys={"v1": "unit-test-root-key-v1", "v2": "unit-test-root-key-v2"},
        ),
    )
    monkeypatch.setattr(config, "TEMPORAL__PAYLOAD_ENCRYPTION_CACHE_TTL_SECONDS", 0)

    keyring = TemporalEncryptionKeyring()
    assert await keyring.get_current_key_id() == "v1"

    monkeypatch.setattr(
        config,
        "TEMPORAL__PAYLOAD_ENCRYPTION_KEYRING",
        _keyring_json(
            current_key_id="v2",
            keys={"v1": "unit-test-root-key-v1", "v2": "unit-test-root-key-v2"},
        ),
    )

    assert await keyring.get_current_key_id() == "v2"


@pytest.mark.anyio
async def test_keyring_revalidates_derived_keys_after_secret_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Derived keys are not reused after the backing keyring refreshes."""
    monkeypatch.setattr(
        config,
        "TEMPORAL__PAYLOAD_ENCRYPTION_KEYRING",
        _keyring_json(
            current_key_id="v1",
            keys={"v1": "unit-test-root-key-v1", "v2": "unit-test-root-key-v2"},
        ),
    )
    monkeypatch.setattr(config, "TEMPORAL__PAYLOAD_ENCRYPTION_CACHE_TTL_SECONDS", 3600)

    keyring = TemporalEncryptionKeyring()
    await keyring.get_key("ws-1", "v1")

    monkeypatch.setattr(
        config,
        "TEMPORAL__PAYLOAD_ENCRYPTION_KEYRING",
        _keyring_json(
            current_key_id="v2",
            keys={"v2": "unit-test-root-key-v2"},
        ),
    )
    monkeypatch.setattr(temporal_codec, "_KEYRING_CACHE_EXPIRES_AT", 0.0)

    with pytest.raises(
        TemporalPayloadCodecError,
        match="keyring does not contain the requested key id",
    ):
        await keyring.get_key("ws-1", "v1")


@pytest.mark.anyio
async def test_payload_encode_resolves_current_key_from_one_keyring_snapshot() -> None:
    """Encode should derive key material from the same keyring snapshot as key id."""

    class RefreshingKeyring(TemporalEncryptionKeyring):
        retrieve_calls = 0

        async def _retrieve_keyring(self) -> tuple[TemporalPayloadKeyring, int]:
            self.retrieve_calls += 1
            if self.retrieve_calls == 1:
                return (
                    TemporalPayloadKeyring.model_validate(
                        {
                            "current_key_id": "v1",
                            "keys": {"v1": "unit-test-root-key-v1"},
                        }
                    ),
                    1,
                )
            return (
                TemporalPayloadKeyring.model_validate(
                    {
                        "current_key_id": "v2",
                        "keys": {"v2": "unit-test-root-key-v2"},
                    }
                ),
                2,
            )

    keyring = RefreshingKeyring()
    codec = EncryptionPayloadCodec(enabled=True, keyring=keyring)
    payload = Payload(metadata={"encoding": b"json/plain"}, data=b'{"secret":"new"}')

    token = _set_workspace_role()
    try:
        encoded = await codec.encode([payload])
    finally:
        ctx_role.reset(token)

    assert keyring.retrieve_calls == 1
    assert encoded[0].metadata["tracecat_key_id"] == b"v1"


@pytest.mark.anyio
async def test_keyring_rotation_keeps_old_payloads_decodable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Old payloads decode when the keyring keeps prior keys after rotation."""
    monkeypatch.setattr(config, "TEMPORAL__PAYLOAD_ENCRYPTION_ENABLED", True)
    monkeypatch.setattr(
        config,
        "TEMPORAL__PAYLOAD_ENCRYPTION_KEYRING",
        _keyring_json(
            current_key_id="v1",
            keys={"v1": "unit-test-root-key-v1", "v2": "unit-test-root-key-v2"},
        ),
    )

    codec = EncryptionPayloadCodec(enabled=True)
    payload = Payload(metadata={"encoding": b"json/plain"}, data=b'{"secret":"old"}')

    token = _set_workspace_role()
    try:
        encrypted_v1 = await codec.encode([payload])
    finally:
        ctx_role.reset(token)
    assert encrypted_v1[0].metadata["tracecat_key_id"] == b"v1"

    reset_temporal_payload_secret_cache()
    monkeypatch.setattr(
        config,
        "TEMPORAL__PAYLOAD_ENCRYPTION_KEYRING",
        _keyring_json(
            current_key_id="v2",
            keys={"v1": "unit-test-root-key-v1", "v2": "unit-test-root-key-v2"},
        ),
    )
    rotated_codec = EncryptionPayloadCodec(enabled=True)

    decoded = await rotated_codec.decode(encrypted_v1)
    assert decoded[0].data == payload.data
    assert decoded[0].metadata["encoding"] == b"json/plain"

    token = _set_workspace_role()
    try:
        encrypted_v2 = await rotated_codec.encode([payload])
    finally:
        ctx_role.reset(token)
    assert encrypted_v2[0].metadata["tracecat_key_id"] == b"v2"


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


@pytest.mark.anyio
async def test_compression_codec_rejects_invalid_algorithm_when_enabled() -> None:
    """Enabled compression rejects invalid algorithms before storing payloads."""
    codec = CompressionPayloadCodec(threshold_bytes=1, algorithm="zstdd", enabled=True)
    payload = Payload(metadata={"encoding": b"json/plain"}, data=b"a" * 100)

    with pytest.raises(ValueError, match="Unsupported compression algorithm: zstdd"):
        await codec.encode([payload])


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
async def test_decode_payloads_passes_through_unencrypted_payloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Historical unencrypted payloads remain readable when encryption is enabled."""
    _enable_encryption(monkeypatch)
    payload = Payload(metadata={"encoding": b"json/plain"}, data=b'{"plain":true}')

    result = await decode_payloads([payload])

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
async def test_decode_payloads_decompresses_with_invalid_current_algorithm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Decode remains metadata-driven when current compression config is invalid."""
    compress_codec = CompressionPayloadCodec(
        threshold_bytes=1, algorithm="zstd", enabled=True
    )
    payload = Payload(metadata={"encoding": b"json/plain"}, data=b'{"key":"value"}')
    compressed = await compress_codec.encode([payload])

    monkeypatch.setattr(config, "TRACECAT__CONTEXT_COMPRESSION_ALGORITHM", "zstdd")

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
