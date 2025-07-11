"""Temporal PayloadCodec for compressing large workflow payloads."""

from collections.abc import Iterable

import cramjam
from loguru import logger
from temporalio.api.common.v1 import Payload
from temporalio.converter import PayloadCodec

from tracecat.concurrency import cooperative
from tracecat.config import (
    TRACECAT__CONTEXT_COMPRESSION_ALGORITHM,
    TRACECAT__CONTEXT_COMPRESSION_ENABLED,
    TRACECAT__CONTEXT_COMPRESSION_THRESHOLD_KB,
)


class CompressionPayloadCodec(PayloadCodec):
    """Temporal PayloadCodec that compresses large payloads using zstd/gzip/brotli.

    This codec automatically compresses payloads that exceed a configurable size
    threshold, helping workflows handle large data without hitting Temporal's
    payload size limits.
    """

    def __init__(
        self,
        threshold_bytes: int | None = None,
        algorithm: str | None = None,
        enabled: bool | None = None,
    ):
        self.enabled = (
            enabled if enabled is not None else TRACECAT__CONTEXT_COMPRESSION_ENABLED
        )
        self.threshold = (
            threshold_bytes
            if threshold_bytes is not None
            else TRACECAT__CONTEXT_COMPRESSION_THRESHOLD_KB * 1024
        )
        self.algorithm = algorithm or TRACECAT__CONTEXT_COMPRESSION_ALGORITHM

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

        result = []
        async for payload in cooperative(payloads):
            # Check if payload size exceeds threshold
            if len(payload.data) <= self.threshold:
                result.append(payload)
                continue

            try:
                # Compress the payload data
                if self.algorithm == "zstd":
                    compressed_data = bytes(cramjam.zstd.compress(payload.data, 11))  # type: ignore
                    encoding = b"binary/zstd"
                elif self.algorithm == "gzip":
                    compressed_data = bytes(cramjam.gzip.compress(payload.data))  # type: ignore
                    encoding = b"binary/gzip"
                elif self.algorithm == "brotli":
                    compressed_data = bytes(cramjam.brotli.compress(payload.data))  # type: ignore
                    encoding = b"binary/brotli"
                else:
                    logger.warning(f"Unknown compression algorithm: {self.algorithm}")
                    result.append(payload)
                    continue

                # Calculate compression ratio
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

                # Create new payload with compression metadata
                # Store the original encoding so we can restore it later
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

                result.append(
                    Payload(
                        metadata=new_metadata,
                        data=compressed_data,
                    )
                )

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
        result = []
        async for payload in cooperative(payloads):
            encoding = payload.metadata.get("encoding", b"").decode()

            # If not compressed, return as-is
            if not encoding.startswith("binary/"):
                result.append(payload)
                continue

            try:
                # Decompress based on encoding
                if encoding == "binary/zstd":
                    decompressed_data = bytes(cramjam.zstd.decompress(payload.data))  # type: ignore
                elif encoding == "binary/gzip":
                    decompressed_data = bytes(cramjam.gzip.decompress(payload.data))  # type: ignore
                elif encoding == "binary/brotli":
                    decompressed_data = bytes(cramjam.brotli.decompress(payload.data))  # type: ignore
                else:
                    logger.warning(f"Unknown compression encoding: {encoding}")
                    result.append(payload)
                    continue

                # Create new payload with original metadata restored
                # Restore the original encoding that was preserved during compression
                original_encoding = payload.metadata.get("original_encoding", b"")
                new_metadata = {
                    k: v
                    for k, v in payload.metadata.items()
                    if k
                    not in (
                        "encoding",
                        "original_encoding",
                        "original_size",
                        "compressed_size",
                    )
                }
                # Restore the original encoding
                if original_encoding:
                    new_metadata["encoding"] = original_encoding

                logger.debug(
                    "Decompressed payload",
                    original_size=payload.metadata.get("original_size", b"").decode(),
                    compressed_size=payload.metadata.get(
                        "compressed_size", b""
                    ).decode(),
                    encoding=encoding,
                )

                result.append(
                    Payload(
                        metadata=new_metadata,
                        data=decompressed_data,
                    )
                )

            except Exception as e:
                logger.error(
                    "Failed to decompress payload",
                    encoding=encoding,
                    compressed_size=len(payload.data),
                    error=str(e),
                )
                # Return the compressed payload as-is if decompression fails
                result.append(payload)

        return result


# Global codec instance
_compression_codec_instance: CompressionPayloadCodec | None = None


def get_compression_payload_codec() -> CompressionPayloadCodec:
    """Get the global compression payload codec instance."""
    global _compression_codec_instance
    if _compression_codec_instance is None:
        _compression_codec_instance = CompressionPayloadCodec()
    return _compression_codec_instance
