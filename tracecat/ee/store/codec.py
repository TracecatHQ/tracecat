"""Implementation of the Temporal Large Payload Codec."""

from __future__ import annotations

import base64
import hashlib

from temporalio.api.common.v1 import Payload
from temporalio.converter import PayloadCodec

from tracecat.ee.store.constants import REMOTE_CODEC_KEY, REMOTE_CODEC_VERSION
from tracecat.ee.store.enums import CodecMode
from tracecat.ee.store.models import ObjectRef
from tracecat.ee.store.object_store import ObjectStore, get_store
from tracecat.logger import logger


class LargePayloadCodec(PayloadCodec):
    """Codec for handling large payloads in Temporal workflows.

    This codec automatically stores large payloads in a remote service and
    replaces them with references in the workflow history. When the payload
    is needed, it transparently retrieves it from the remote service.
    """

    def __init__(
        self,
        namespace: str,
        store: ObjectStore | None = None,
        min_bytes: int = 128_000,
        version: str = "v2",
        skip_url_health_check: bool = False,
        disable_encoding: bool = False,
        custom_headers: dict[str, list[str]] | None = None,
        mode: CodecMode = CodecMode.ALWAYS,
    ) -> None:
        """Initialize the Large Payload Codec.

        Args:
            namespace: The Temporal namespace
            min_bytes: The minimum size in bytes to use remote codec (default 128KB)
            store: The object store to use
            version: The LPS API version (only v2 supported)
            skip_url_health_check: When True, skip URL health check
            disable_encoding: When True, encoding will be disabled
            custom_headers: Custom HTTP headers for requests
            mode: The mode to use for encoding (default "always")
        Raises:
            ValueError: If required parameters are missing or invalid
        """
        # Validate required fields
        if not namespace:
            raise ValueError("A namespace is required")

        self.namespace = namespace
        self.min_bytes = min_bytes
        self.mode = mode
        self.store = store or get_store()
        self.version = version
        self.skip_url_health_check = skip_url_health_check
        self.disable_encoding = disable_encoding
        self.custom_headers = custom_headers or {}
        self.logger = logger.bind(service="large_payload_codec")
        if self.version != "v2":
            raise ValueError(f"Invalid codec version: {self.version}")

    @staticmethod
    def digest(data: bytes) -> str:
        """Compute the SHA-256 digest of the data."""
        return f"sha256:{hashlib.sha256(data).hexdigest()}"

    @staticmethod
    def key(namespace: str, digest: str) -> str:
        return f"blobs/{namespace}/{digest}"

    def _validate_digest(self, digest: str, data: bytes) -> None:
        # Verify checksum
        digest_parts = digest.split(":")
        if len(digest_parts) != 2 or digest_parts[0] != "sha256":
            raise ValueError(f"Invalid digest format: {digest}")

        expected_digest = digest_parts[1]
        actual_digest = hashlib.sha256(data).hexdigest()
        if actual_digest != expected_digest:
            raise ValueError(
                f"Checksum mismatch: expected {expected_digest}, got {actual_digest}"
            )

    async def encode(self, payloads: list[Payload]) -> list[Payload]:
        """Encode large payloads by storing them in the remote service.

        Args:
            payloads: List of payloads to potentially encode

        Returns:
            List of encoded payloads

        Raises:
            ValueError: If there's an error communicating with the remote service
        """
        self.logger.warning("Encoding payloads with LargePayloadCodec")
        if not payloads or self.disable_encoding:
            return payloads

        result: list[Payload] = []
        for payload in payloads:
            # Check if payload is large enough to be encoded
            if self.mode == CodecMode.THRESHOLD and len(payload.data) < self.min_bytes:
                result.append(payload)
                continue

            # Compute payload digest
            digest = self.digest(payload.data)

            # PUT payload to remote service
            key = await self._put_payload(payload, digest)

            # Create remote reference payload
            metadata = {
                "encoding": b"json/plain",
                REMOTE_CODEC_KEY: REMOTE_CODEC_VERSION.encode(),
            }

            # Convert original metadata to base64 strings for JSON serialization
            metadata_json: dict[str, str] = {}
            for k, v in payload.metadata.items():
                # Always encode value as base64
                value_b64 = base64.b64encode(v).decode()
                metadata_json[k] = value_b64
            self.logger.warning("Encoded metadata", metadata=metadata_json)

            remote_data = ObjectRef(
                metadata=metadata,
                size=len(payload.data),
                digest=digest,
                key=key,
            )

            remote_payload = Payload(
                metadata=metadata,
                data=remote_data.model_dump_json().encode(),
            )
            result.append(remote_payload)

        return result

    async def decode(self, payloads: list[Payload]) -> list[Payload]:
        """Decode payload references by retrieving the data from the remote service.

        Args:
            payloads: List of payloads to potentially decode

        Returns:
            List of decoded payloads

        Raises:
            ValueError: If there's an error with the payload format or remote service
        """
        self.logger.warning("Decoding payloads with LargePayloadCodec")
        if not payloads:
            return payloads

        result: list[Payload] = []
        for payload in payloads:
            # Check if this is a remote payload
            if REMOTE_CODEC_KEY not in payload.metadata:
                result.append(payload)
                continue

            if (
                codec_version := payload.metadata[REMOTE_CODEC_KEY]
            ) != REMOTE_CODEC_VERSION.encode():
                raise ValueError(f"Unsupported remote codec version: {codec_version}")

            # Parse remote payload data
            self.logger.warning("Decoding payload", data=payload.data)
            obj_ref = ObjectRef.model_validate_json(payload.data)

            # GET payload from remote service
            self.logger.debug("Getting payload", key=obj_ref.key, size=obj_ref.size)
            original_data = await self._get_payload(obj_ref.key, obj_ref.size)
            self.logger.debug("Got payload", key=obj_ref.key, size=obj_ref.size)
            self._validate_digest(obj_ref.digest, original_data)
            # Reconstruct original payload
            original_metadata: dict[str, bytes] = {}
            self.logger.warning("Decoding metadata", metadata=obj_ref.metadata)
            for k_str, v_b64 in obj_ref.metadata.items():
                # Decode base64 value
                try:
                    value_bytes = base64.b64decode(v_b64)
                except Exception as e:
                    self.logger.warning(
                        "Couldn't interpret field as base64",
                        field=k_str,
                        value=v_b64,
                        error=e,
                    )
                    value_bytes = v_b64
                original_metadata[k_str] = value_bytes
            self.logger.warning("Decoded metadata", metadata=original_metadata)

            original_payload = Payload(metadata=original_metadata, data=original_data)
            result.append(original_payload)

        return result

    async def _put_payload(self, payload: Payload, digest: str) -> str:
        """Send a large payload to the remote service.

        Args:
            payload: The payload to store
            digest: SHA-256 digest of the payload data

        Returns:
            The key to retrieve the payload

        Raises:
            ValueError: If there's an error communicating with the remote service
        """

        key = self.key(self.namespace, digest)
        await self.store._put(
            Bucket=self.store.bucket_name,
            Key=key,
            Body=payload.data,
        )
        return key

    async def _get_payload(self, key: str, expected_size: int) -> bytes:
        """Retrieve a payload from the remote service.

        Args:
            key: The key to retrieve the payload
            expected_size: Expected size of the payload in bytes

        Returns:
            The retrieved payload data

        Raises:
            ValueError: If there's an error communicating with the remote service
        """
        response = await self.store._get(
            Bucket=self.store.bucket_name,
            Key=key,
        )
        async with response["Body"] as body:
            data = await body.read()
        if len(data) != expected_size:
            self.logger.warning(
                "Payload size mismatch",
                key=key,
                expected_size=expected_size,
                actual_size=len(data),
            )
        return data
