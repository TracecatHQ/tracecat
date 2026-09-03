from __future__ import annotations

import asyncio
import base64
import os
from collections.abc import Iterable, Sequence
from functools import cache
from threading import Lock
from time import monotonic
from typing import Final, Self

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from cachetools import TTLCache
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from loguru import logger
from pydantic import (
    BaseModel,
    ConfigDict,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)
from temporalio.api.common.v1 import Payload
from temporalio.converter import PayloadCodec

from tracecat import config
from tracecat.concurrency import cooperative
from tracecat.contexts import ctx_role

TRACECAT_TEMPORAL_ENCODING: Final[bytes] = b"binary/tracecat-aes256gcm"
TRACECAT_TEMPORAL_GLOBAL_SCOPE: Final[str] = "__global__"
_COMPRESSION_ALGORITHMS: Final[frozenset[str]] = frozenset({"zstd", "gzip", "brotli"})
_TRACECAT_TEMPORAL_ENCRYPTION_METADATA_KEYS: Final[frozenset[str]] = frozenset(
    {
        "tracecat_original_encoding",
        "tracecat_workspace_id",
        "tracecat_key_id",
        "tracecat_nonce",
    }
)


class TemporalPayloadCodecError(RuntimeError):
    """Raised when Temporal payload encryption/decryption fails."""


class TemporalPayloadKeyring(BaseModel):
    """Versioned Temporal payload encryption keyring."""

    model_config = ConfigDict(frozen=True)

    current_key_id: str
    keys: dict[str, SecretStr]

    @field_validator("current_key_id")
    @classmethod
    def validate_current_key_id(cls, value: str) -> str:
        """Validate the current key id is usable."""
        if not value:
            raise ValueError("current_key_id cannot be empty")
        return value

    @field_validator("keys")
    @classmethod
    def validate_keys(cls, value: dict[str, SecretStr]) -> dict[str, SecretStr]:
        """Validate keyring entries are usable."""
        if not value:
            raise ValueError("keys cannot be empty")

        for key_id, root_secret in value.items():
            if not key_id:
                raise ValueError("key ids cannot be empty")
            if not root_secret.get_secret_value():
                raise ValueError("root secrets cannot be empty")
        return value

    @model_validator(mode="after")
    def validate_current_key_is_present(self) -> Self:
        """Validate the current key id exists in the keyring."""
        if self.current_key_id not in self.keys:
            raise ValueError("current_key_id must be present in keys")
        return self

    def root_secret_for_key_id(self, key_id: str) -> bytes:
        """Return the root secret bytes for a key id."""
        if (root_secret := self.keys.get(key_id)) is None:
            raise TemporalPayloadCodecError(
                "Temporal payload encryption keyring does not contain the requested key id"
            )
        return root_secret.get_secret_value().encode("utf-8")


_KEYRING_LOCK = Lock()
_KEYRING_CACHE: TemporalPayloadKeyring | None = None
_KEYRING_CACHE_EXPIRES_AT = 0.0
_KEYRING_CACHE_GENERATION = 0
_KEYRING_REFRESH_FAILURE_BACKOFF_SECONDS: Final[float] = 30.0


class CompressionPayloadCodec(PayloadCodec):
    """Temporal PayloadCodec that compresses large workflow payloads."""

    def __init__(
        self,
        threshold_bytes: int | None = None,
        algorithm: str | None = None,
        enabled: bool | None = None,
    ):
        from cramjam import (
            brotli as cramjam_brotli,  # pyright: ignore[reportAttributeAccessIssue]
        )
        from cramjam import (
            gzip as cramjam_gzip,  # pyright: ignore[reportAttributeAccessIssue]
        )
        from cramjam import (
            zstd as cramjam_zstd,  # pyright: ignore[reportAttributeAccessIssue]
        )

        self._brotli = cramjam_brotli
        self._gzip = cramjam_gzip
        self._zstd = cramjam_zstd
        self.enabled = (
            enabled
            if enabled is not None
            else config.TRACECAT__CONTEXT_COMPRESSION_ENABLED
        )
        self.threshold = (
            threshold_bytes
            if threshold_bytes is not None
            else config.TRACECAT__CONTEXT_COMPRESSION_THRESHOLD_KB * 1024
        )
        self.algorithm = algorithm or config.TRACECAT__CONTEXT_COMPRESSION_ALGORITHM

        logger.debug(
            "Compression codec initialized",
            enabled=self.enabled,
            threshold=self.threshold,
            algorithm=self.algorithm,
        )

    async def encode(self, payloads: Iterable[Payload]) -> list[Payload]:
        """Encode payloads, compressing those that exceed the threshold."""
        if not self.enabled:
            return list(payloads)
        if self.algorithm not in _COMPRESSION_ALGORITHMS:
            supported = ", ".join(sorted(_COMPRESSION_ALGORITHMS))
            raise ValueError(
                f"Unsupported compression algorithm: {self.algorithm}. "
                f"Supported algorithms: {supported}"
            )

        result: list[Payload] = []
        async for payload in cooperative(payloads):
            if len(payload.data) <= self.threshold:
                result.append(payload)
                continue

            try:
                match self.algorithm:
                    case "zstd":
                        compressed_data = bytes(self._zstd.compress(payload.data, 11))
                        encoding = b"binary/zstd"
                    case "gzip":
                        compressed_data = bytes(self._gzip.compress(payload.data))
                        encoding = b"binary/gzip"
                    case "brotli":
                        compressed_data = bytes(self._brotli.compress(payload.data))
                        encoding = b"binary/brotli"
                    case _:
                        logger.warning(
                            "Unknown compression algorithm, storing payload as-is",
                            algorithm=self.algorithm,
                        )
                        result.append(payload)
                        continue

                original_size = len(payload.data)
                compressed_size = len(compressed_data)
                compression_ratio = (
                    original_size / compressed_size if compressed_size > 0 else 1.0
                )

                logger.debug(
                    "Compressed payload",
                    original_size=original_size,
                    compressed_size=compressed_size,
                    compression_ratio=f"{compression_ratio:.2f}x",
                    algorithm=self.algorithm,
                )

                original_encoding = payload.metadata.get("encoding", b"")
                new_metadata = dict(payload.metadata)
                new_metadata.update(
                    {
                        "encoding": encoding,
                        "original_encoding": original_encoding,
                        "original_size": str(original_size).encode(),
                        "compressed_size": str(compressed_size).encode(),
                    }
                )

                result.append(Payload(metadata=new_metadata, data=compressed_data))
            except Exception as e:
                logger.error(
                    "Failed to compress payload, storing uncompressed",
                    original_size=len(payload.data),
                    algorithm=self.algorithm,
                    error=str(e),
                )
                result.append(payload)

        return result

    async def decode(self, payloads: Iterable[Payload]) -> list[Payload]:
        """Decode payloads, decompressing those that were compressed."""
        result: list[Payload] = []
        async for payload in cooperative(payloads):
            encoding = payload.metadata.get("encoding", b"").decode()
            if not encoding.startswith("binary/") or encoding == "binary/null":
                result.append(payload)
                continue

            try:
                match encoding:
                    case "binary/zstd":
                        decompressed_data = bytes(self._zstd.decompress(payload.data))
                    case "binary/gzip":
                        decompressed_data = bytes(self._gzip.decompress(payload.data))
                    case "binary/brotli":
                        decompressed_data = bytes(self._brotli.decompress(payload.data))
                    case _:
                        result.append(payload)
                        continue

                original_encoding = payload.metadata.get("original_encoding", b"")
                new_metadata = {
                    key: value
                    for key, value in payload.metadata.items()
                    if key
                    not in (
                        "encoding",
                        "original_encoding",
                        "original_size",
                        "compressed_size",
                    )
                }
                if original_encoding:
                    new_metadata["encoding"] = original_encoding

                result.append(Payload(metadata=new_metadata, data=decompressed_data))
            except Exception as e:
                logger.error(
                    "Failed to decompress payload",
                    encoding=encoding,
                    compressed_size=len(payload.data),
                    error=str(e),
                )
                result.append(payload)
        return result


class TemporalEncryptionKeyring:
    """Derive and cache workspace-scoped Temporal payload keys."""

    def __init__(self) -> None:
        self._cache: TTLCache[tuple[str, str, int], bytes] = TTLCache(
            maxsize=config.TEMPORAL__PAYLOAD_ENCRYPTION_CACHE_MAX_ITEMS,
            ttl=config.TEMPORAL__PAYLOAD_ENCRYPTION_CACHE_TTL_SECONDS,
        )
        self._lock = Lock()

    def _parse_keyring(self, secret: str) -> TemporalPayloadKeyring:
        """Parse and validate a Temporal payload encryption keyring."""
        try:
            return TemporalPayloadKeyring.model_validate_json(secret)
        except ValidationError as e:
            raise TemporalPayloadCodecError(
                "Temporal payload encryption keyring is invalid"
            ) from e

    def _retrieve_keyring_from_aws(self, arn: str) -> TemporalPayloadKeyring:
        """Retrieve the Temporal payload keyring from AWS Secrets Manager."""
        try:
            session = boto3.session.Session()
            client = session.client(service_name="secretsmanager")
            response = client.get_secret_value(SecretId=arn)
        except (BotoCoreError, ClientError) as e:
            raise TemporalPayloadCodecError(
                "Failed to retrieve Temporal payload encryption keyring"
            ) from e

        match response:
            case {"SecretString": str(secret_string)} if secret_string:
                return self._parse_keyring(secret_string)
            case {"SecretBinary": bytes(secret_binary)}:
                try:
                    secret_string = secret_binary.decode("utf-8")
                except UnicodeDecodeError as e:
                    raise TemporalPayloadCodecError(
                        "Temporal payload encryption keyring SecretBinary is invalid"
                    ) from e
                if secret_string:
                    return self._parse_keyring(secret_string)

        raise TemporalPayloadCodecError(
            "Temporal payload encryption keyring secret is empty"
        )

    async def _retrieve_keyring(self) -> tuple[TemporalPayloadKeyring, int]:
        global _KEYRING_CACHE, _KEYRING_CACHE_EXPIRES_AT, _KEYRING_CACHE_GENERATION
        now = monotonic()
        with _KEYRING_LOCK:
            if _KEYRING_CACHE is not None and now < _KEYRING_CACHE_EXPIRES_AT:
                return _KEYRING_CACHE, _KEYRING_CACHE_GENERATION

        arn = config.TEMPORAL__PAYLOAD_ENCRYPTION_KEYRING_ARN
        keyring_json = config.TEMPORAL__PAYLOAD_ENCRYPTION_KEYRING
        try:
            if arn:
                keyring = await asyncio.to_thread(self._retrieve_keyring_from_aws, arn)
            elif keyring_json:
                keyring = self._parse_keyring(keyring_json)
            else:
                raise TemporalPayloadCodecError(
                    "Temporal payload encryption is enabled but no keyring is configured"
                )
        except TemporalPayloadCodecError as e:
            if arn or keyring_json:
                with _KEYRING_LOCK:
                    if _KEYRING_CACHE is not None:
                        _KEYRING_CACHE_EXPIRES_AT = (
                            monotonic() + _KEYRING_REFRESH_FAILURE_BACKOFF_SECONDS
                        )
                        logger.warning(
                            "Failed to refresh Temporal payload encryption keyring; "
                            "using cached keyring",
                            error=type(e).__name__,
                        )
                        return _KEYRING_CACHE, _KEYRING_CACHE_GENERATION
            raise

        with _KEYRING_LOCK:
            now = monotonic()
            if _KEYRING_CACHE is not None and now < _KEYRING_CACHE_EXPIRES_AT:
                return _KEYRING_CACHE, _KEYRING_CACHE_GENERATION
            _KEYRING_CACHE = keyring
            _KEYRING_CACHE_EXPIRES_AT = (
                now + config.TEMPORAL__PAYLOAD_ENCRYPTION_CACHE_TTL_SECONDS
            )
            _KEYRING_CACHE_GENERATION += 1
            return _KEYRING_CACHE, _KEYRING_CACHE_GENERATION

    @staticmethod
    def _derive_key(
        workspace_id: str, key_id: str, keyring: TemporalPayloadKeyring
    ) -> bytes:
        root_secret = keyring.root_secret_for_key_id(key_id)
        salt = f"tracecat-temporal-payload:{key_id}".encode()
        info = workspace_id.encode("utf-8")
        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            info=info,
        )
        return hkdf.derive(root_secret)

    def _get_or_derive_key(
        self,
        *,
        workspace_id: str,
        key_id: str,
        keyring: TemporalPayloadKeyring,
        generation: int,
    ) -> bytes:
        cache_key = (workspace_id, key_id, generation)
        with self._lock:
            if (key := self._cache.get(cache_key)) is not None:
                return key

        key = self._derive_key(workspace_id, key_id, keyring)
        with self._lock:
            if (cached := self._cache.get(cache_key)) is not None:
                return cached
            self._cache[cache_key] = key
            return key

    async def get_current_key_id(self) -> str:
        """Return the current Temporal payload key id for new encryptions."""
        keyring, _generation = await self._retrieve_keyring()
        return keyring.current_key_id

    async def get_current_key(self, workspace_id: str) -> tuple[str, bytes]:
        """Return the current key id and derived key from one keyring snapshot."""
        keyring, generation = await self._retrieve_keyring()
        key_id = keyring.current_key_id
        key = self._get_or_derive_key(
            workspace_id=workspace_id,
            key_id=key_id,
            keyring=keyring,
            generation=generation,
        )
        return key_id, key

    async def get_key(self, workspace_id: str, key_id: str) -> bytes:
        """Return the derived encryption key for a workspace and key id."""
        keyring, generation = await self._retrieve_keyring()
        return self._get_or_derive_key(
            workspace_id=workspace_id,
            key_id=key_id,
            keyring=keyring,
            generation=generation,
        )

    def clear(self) -> None:
        """Clear the derived key cache."""
        with self._lock:
            self._cache.clear()


class EncryptionPayloadCodec(PayloadCodec):
    """Temporal payload codec that encrypts payload bytes in place."""

    def __init__(
        self,
        *,
        enabled: bool | None = None,
        keyring: TemporalEncryptionKeyring | None = None,
    ) -> None:
        self.enabled = (
            enabled
            if enabled is not None
            else config.TEMPORAL__PAYLOAD_ENCRYPTION_ENABLED
        )
        self.keyring = keyring or TemporalEncryptionKeyring()

    @staticmethod
    def _resolve_workspace_scope() -> str:
        """Resolve the workspace scope for payload encryption.

        Uses ctx_role.workspace_id when set; falls back to
        TRACECAT_TEMPORAL_GLOBAL_SCOPE for platform-scoped operations
        (e.g. registry sync) where the role has no workspace.
        """
        if (role := ctx_role.get()) and role.workspace_id is not None:
            return str(role.workspace_id)
        return TRACECAT_TEMPORAL_GLOBAL_SCOPE

    @staticmethod
    def _build_aad(
        *,
        workspace_id: str,
        key_id: str,
        original_encoding: bytes,
    ) -> bytes:
        """Build authenticated data for encrypted payload metadata."""
        parts = (
            workspace_id.encode("utf-8"),
            key_id.encode("utf-8"),
            TRACECAT_TEMPORAL_ENCODING,
            original_encoding,
        )
        return b"".join(len(part).to_bytes(4, "big") + part for part in parts)

    @staticmethod
    def _has_encryption_metadata(payload: Payload) -> bool:
        """Return whether payload metadata looks like Tracecat encrypted metadata."""
        return any(
            key in payload.metadata
            for key in _TRACECAT_TEMPORAL_ENCRYPTION_METADATA_KEYS
        )

    async def encode(self, payloads: Iterable[Payload]) -> list[Payload]:
        """Encrypt payloads."""
        if not self.enabled:
            return list(payloads)

        workspace_id = self._resolve_workspace_scope()
        key_id, key = await self.keyring.get_current_key(workspace_id)
        aesgcm = AESGCM(key)

        result: list[Payload] = []
        async for payload in cooperative(payloads):
            encoding = payload.metadata.get("encoding", b"")
            if encoding == b"binary/null" or not payload.data:
                result.append(payload)
                continue

            try:
                nonce = os.urandom(12)
                aad = self._build_aad(
                    workspace_id=workspace_id,
                    key_id=key_id,
                    original_encoding=encoding,
                )
                ciphertext = aesgcm.encrypt(nonce, payload.data, aad)
                metadata = dict(payload.metadata)
                metadata.update(
                    {
                        "encoding": TRACECAT_TEMPORAL_ENCODING,
                        "tracecat_original_encoding": encoding,
                        "tracecat_workspace_id": workspace_id.encode("utf-8"),
                        "tracecat_key_id": key_id.encode("utf-8"),
                        "tracecat_nonce": base64.urlsafe_b64encode(nonce),
                    }
                )
                result.append(Payload(metadata=metadata, data=ciphertext))
            except Exception as e:
                logger.error(
                    "Failed to encrypt Temporal payload",
                    error=type(e).__name__,
                    payload_size=len(payload.data),
                )
                raise TemporalPayloadCodecError(
                    "Failed to encrypt Temporal payload"
                ) from e

        return result

    async def decode(self, payloads: Iterable[Payload]) -> list[Payload]:
        """Decrypt payloads."""
        result: list[Payload] = []
        async for payload in cooperative(payloads):
            if payload.metadata.get("encoding", b"") != TRACECAT_TEMPORAL_ENCODING:
                if self._has_encryption_metadata(payload):
                    raise TemporalPayloadCodecError(
                        "Encrypted Temporal payload has an invalid encoding marker"
                    )
                result.append(payload)
                continue

            try:
                workspace_id = payload.metadata.get(
                    "tracecat_workspace_id", b""
                ).decode("utf-8")
                if not workspace_id:
                    raise TemporalPayloadCodecError(
                        "Encrypted Temporal payload is missing its workspace scope"
                    )
                key_id = payload.metadata.get("tracecat_key_id", b"").decode("utf-8")
                if not key_id:
                    raise TemporalPayloadCodecError(
                        "Encrypted Temporal payload is missing its key id"
                    )
                nonce_b64 = payload.metadata.get("tracecat_nonce")
                if nonce_b64 is None:
                    raise TemporalPayloadCodecError(
                        "Encrypted Temporal payload is missing its nonce"
                    )
                original_encoding = payload.metadata.get("tracecat_original_encoding")
                if original_encoding is None:
                    raise TemporalPayloadCodecError(
                        "Encrypted Temporal payload is missing its original encoding"
                    )

                aesgcm = AESGCM(await self.keyring.get_key(workspace_id, key_id))
                aad = self._build_aad(
                    workspace_id=workspace_id,
                    key_id=key_id,
                    original_encoding=original_encoding,
                )
                plaintext = aesgcm.decrypt(
                    base64.urlsafe_b64decode(nonce_b64),
                    payload.data,
                    aad,
                )

                metadata = {
                    key: value
                    for key, value in payload.metadata.items()
                    if key not in _TRACECAT_TEMPORAL_ENCRYPTION_METADATA_KEYS
                    and key != "encoding"
                }
                if original_encoding:
                    metadata["encoding"] = original_encoding
                result.append(Payload(metadata=metadata, data=plaintext))
            except Exception as e:
                logger.error(
                    "Failed to decrypt Temporal payload",
                    error=type(e).__name__,
                    payload_size=len(payload.data),
                )
                raise TemporalPayloadCodecError(
                    "Failed to decrypt Temporal payload"
                ) from e
        return result


class CompositePayloadCodec(PayloadCodec):
    """Apply multiple payload codecs in order on encode and reverse order on decode."""

    def __init__(self, codecs: Sequence[PayloadCodec]) -> None:
        self.codecs = tuple(codecs)

    async def encode(self, payloads: Iterable[Payload]) -> list[Payload]:
        current = list(payloads)
        for codec in self.codecs:
            current = await codec.encode(current)
        return current

    async def decode(self, payloads: Iterable[Payload]) -> list[Payload]:
        current = list(payloads)
        for codec in reversed(self.codecs):
            current = await codec.decode(current)
        return current


@cache
def get_payload_codec(*, compression_enabled: bool = False) -> PayloadCodec:
    """Build the payload codec chain.

    Encode is gated by each codec's ``enabled`` flag (driven by config).
    Decode always includes all codecs so historical payloads remain readable
    regardless of current config — each codec's ``decode`` checks the encoding
    marker and is a no-op for payloads it didn't produce.
    """
    return CompositePayloadCodec(
        [
            CompressionPayloadCodec(enabled=compression_enabled),
            EncryptionPayloadCodec(),
        ]
    )


def reset_temporal_payload_codec_cache() -> None:
    """Clear the memoized Temporal payload codec chain."""
    get_payload_codec.cache_clear()


async def decode_payloads(payloads: Sequence[Payload]) -> list[Payload]:
    """Decode payloads using all codecs, regardless of current config."""
    codec = get_payload_codec(compression_enabled=True)
    return await codec.decode(payloads)


def reset_temporal_payload_secret_cache() -> None:
    global _KEYRING_CACHE, _KEYRING_CACHE_EXPIRES_AT, _KEYRING_CACHE_GENERATION
    with _KEYRING_LOCK:
        _KEYRING_CACHE = None
        _KEYRING_CACHE_EXPIRES_AT = 0.0
        _KEYRING_CACHE_GENERATION += 1
