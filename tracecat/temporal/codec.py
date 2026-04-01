from __future__ import annotations

import asyncio
import base64
import os
import time
from collections import OrderedDict
from collections.abc import Iterable, Sequence
from threading import Lock
from typing import Final

import boto3
from botocore.exceptions import ClientError
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from loguru import logger
from temporalio.api.common.v1 import Payload
from temporalio.converter import PayloadCodec

from tracecat import config
from tracecat.concurrency import cooperative
from tracecat.contexts import ctx_role, ctx_temporal_workspace_id

TRACECAT_TEMPORAL_ENCODING: Final[bytes] = b"binary/tracecat-aes256gcm"
TRACECAT_TEMPORAL_GLOBAL_SCOPE: Final[str] = "__global__"
_ROOT_SECRET_LOCK = Lock()
_ROOT_SECRET_CACHE: bytes | None = None


class TemporalPayloadCodecError(RuntimeError):
    """Raised when Temporal payload encryption/decryption fails."""


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

        if self.enabled and self.algorithm not in ("zstd", "gzip", "brotli"):
            raise ValueError(f"Unsupported compression algorithm: {self.algorithm}")

        logger.info(
            "Compression codec initialized",
            enabled=self.enabled,
            threshold=self.threshold,
            algorithm=self.algorithm,
        )

    async def encode(self, payloads: Iterable[Payload]) -> list[Payload]:
        """Encode payloads, compressing those that exceed the threshold."""
        if not self.enabled:
            return list(payloads)

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
        self._cache: OrderedDict[tuple[str, str], tuple[bytes, float]] = OrderedDict()
        self._lock = Lock()

    def _cache_settings(self) -> tuple[int, int]:
        return (
            config.TEMPORAL__PAYLOAD_ENCRYPTION_CACHE_MAX_ITEMS,
            config.TEMPORAL__PAYLOAD_ENCRYPTION_CACHE_TTL_SECONDS,
        )

    def _coerce_root_secret(self, secret: str) -> bytes:
        return secret.encode("utf-8")

    def _retrieve_root_secret_from_aws(self, arn: str) -> bytes:
        """Retrieve the Temporal payload root secret from AWS Secrets Manager."""
        try:
            session = boto3.session.Session()
            client = session.client(service_name="secretsmanager")
            response = client.get_secret_value(SecretId=arn)
        except ClientError as e:
            raise TemporalPayloadCodecError(
                "Failed to retrieve Temporal payload encryption root key"
            ) from e

        match response:
            case {"SecretString": str(secret_string)} if secret_string:
                return self._coerce_root_secret(secret_string)
            case {"SecretBinary": bytes(secret_binary)}:
                if secret_string := base64.b64decode(secret_binary).decode("utf-8"):
                    return self._coerce_root_secret(secret_string)

        raise TemporalPayloadCodecError(
            "Temporal payload encryption root key secret is empty"
        )

    async def _retrieve_root_secret(self) -> bytes:
        global _ROOT_SECRET_CACHE
        with _ROOT_SECRET_LOCK:
            if _ROOT_SECRET_CACHE is not None:
                return _ROOT_SECRET_CACHE

        if secret := config.TEMPORAL__PAYLOAD_ENCRYPTION_KEY:
            secret_bytes = self._coerce_root_secret(secret)
            with _ROOT_SECRET_LOCK:
                if _ROOT_SECRET_CACHE is None:
                    _ROOT_SECRET_CACHE = secret_bytes
                return _ROOT_SECRET_CACHE

        if not (arn := config.TEMPORAL__PAYLOAD_ENCRYPTION_KEY__ARN):
            raise TemporalPayloadCodecError(
                "Temporal payload encryption is enabled but no root key is configured"
            )

        secret_bytes = await asyncio.to_thread(self._retrieve_root_secret_from_aws, arn)
        with _ROOT_SECRET_LOCK:
            if _ROOT_SECRET_CACHE is None:
                _ROOT_SECRET_CACHE = secret_bytes
            return _ROOT_SECRET_CACHE

    async def _derive_key(self, workspace_id: str, key_version: str) -> bytes:
        root_secret = await self._retrieve_root_secret()
        salt = f"tracecat-temporal-payload:{key_version}".encode()
        info = workspace_id.encode("utf-8")
        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            info=info,
        )
        return hkdf.derive(root_secret)

    async def get_key(self, workspace_id: str, key_version: str) -> bytes:
        cache_key = (workspace_id, key_version)
        max_items, ttl_seconds = self._cache_settings()
        now = time.monotonic()
        with self._lock:
            if cache_entry := self._cache.get(cache_key):
                key, expires_at = cache_entry
                if expires_at > now:
                    self._cache.move_to_end(cache_key)
                    return key
                self._cache.pop(cache_key, None)

        key = await self._derive_key(workspace_id, key_version)
        now = time.monotonic()
        with self._lock:
            if cache_entry := self._cache.get(cache_key):
                cached_key, expires_at = cache_entry
                if expires_at > now:
                    self._cache.move_to_end(cache_key)
                    return cached_key
                self._cache.pop(cache_key, None)

            self._cache[cache_key] = (key, now + ttl_seconds)
            while len(self._cache) > max_items:
                self._cache.popitem(last=False)
            return key

    def clear(self) -> None:
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
        if workspace_id := ctx_temporal_workspace_id.get():
            return workspace_id
        if (role := ctx_role.get()) and role.workspace_id is not None:
            return str(role.workspace_id)
        raise TemporalPayloadCodecError(
            "Temporal payload encryption requires an explicit workspace scope"
        )

    async def encode(self, payloads: Iterable[Payload]) -> list[Payload]:
        if not self.enabled:
            return list(payloads)

        workspace_id = self._resolve_workspace_scope()
        key_version = config.TEMPORAL__PAYLOAD_ENCRYPTION_KEY_VERSION
        aesgcm = AESGCM(await self.keyring.get_key(workspace_id, key_version))
        result: list[Payload] = []
        async for payload in cooperative(payloads):
            encoding = payload.metadata.get("encoding", b"")
            if encoding == b"binary/null" or not payload.data:
                result.append(payload)
                continue

            nonce = os.urandom(12)
            aad = b"|".join((workspace_id.encode("utf-8"), key_version.encode("utf-8")))
            ciphertext = aesgcm.encrypt(nonce, payload.data, aad)
            metadata = dict(payload.metadata)
            metadata.update(
                {
                    "encoding": TRACECAT_TEMPORAL_ENCODING,
                    "tracecat_original_encoding": encoding,
                    "tracecat_workspace_id": workspace_id.encode("utf-8"),
                    "tracecat_key_version": key_version.encode("utf-8"),
                    "tracecat_nonce": base64.urlsafe_b64encode(nonce),
                }
            )
            result.append(Payload(metadata=metadata, data=ciphertext))
        return result

    async def decode(self, payloads: Iterable[Payload]) -> list[Payload]:
        result: list[Payload] = []
        async for payload in cooperative(payloads):
            if payload.metadata.get("encoding", b"") != TRACECAT_TEMPORAL_ENCODING:
                result.append(payload)
                continue

            workspace_id = payload.metadata.get("tracecat_workspace_id", b"").decode(
                "utf-8"
            )
            if not workspace_id:
                raise TemporalPayloadCodecError(
                    "Encrypted Temporal payload is missing its workspace scope"
                )
            key_version = (
                payload.metadata.get("tracecat_key_version", b"").decode("utf-8")
                or config.TEMPORAL__PAYLOAD_ENCRYPTION_KEY_VERSION
            )
            nonce_b64 = payload.metadata.get("tracecat_nonce")
            if nonce_b64 is None:
                raise TemporalPayloadCodecError(
                    "Encrypted Temporal payload is missing its nonce"
                )

            aesgcm = AESGCM(await self.keyring.get_key(workspace_id, key_version))
            aad = b"|".join((workspace_id.encode("utf-8"), key_version.encode("utf-8")))
            try:
                plaintext = aesgcm.decrypt(
                    base64.urlsafe_b64decode(nonce_b64),
                    payload.data,
                    aad,
                )
            except Exception as e:
                raise TemporalPayloadCodecError(
                    "Failed to decrypt Temporal payload"
                ) from e

            metadata = {
                key: value
                for key, value in payload.metadata.items()
                if key
                not in {
                    "encoding",
                    "tracecat_original_encoding",
                    "tracecat_workspace_id",
                    "tracecat_key_version",
                    "tracecat_nonce",
                }
            }
            original_encoding = payload.metadata.get("tracecat_original_encoding", b"")
            if original_encoding:
                metadata["encoding"] = original_encoding
            result.append(Payload(metadata=metadata, data=plaintext))
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


def get_payload_codec(*, compression_enabled: bool = False) -> PayloadCodec | None:
    codecs: list[PayloadCodec] = []
    if compression_enabled:
        codecs.append(CompressionPayloadCodec())
    if config.TEMPORAL__PAYLOAD_ENCRYPTION_ENABLED:
        codecs.append(EncryptionPayloadCodec())
    if not codecs:
        return None
    return CompositePayloadCodec(codecs)


async def decode_payloads(
    payloads: Sequence[Payload], *, compression_enabled: bool = False
) -> list[Payload]:
    codec = get_payload_codec(compression_enabled=compression_enabled)
    if codec is None:
        return list(payloads)
    return await codec.decode(payloads)


def reset_temporal_payload_secret_cache() -> None:
    global _ROOT_SECRET_CACHE
    with _ROOT_SECRET_LOCK:
        _ROOT_SECRET_CACHE = None
