"""Backward-compatible import surface for the shared compression codec."""

from tracecat.temporal.codec import CompressionPayloadCodec

_compression_codec_instance: CompressionPayloadCodec | None = None


def get_compression_payload_codec() -> CompressionPayloadCodec:
    global _compression_codec_instance
    if _compression_codec_instance is None:
        _compression_codec_instance = CompressionPayloadCodec()
    return _compression_codec_instance
