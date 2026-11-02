import asyncio
import base64
from types import SimpleNamespace

import pytest
import tracecat_registry.integrations.aws_boto3 as aws_boto3
from aiobotocore.response import StreamingBody


def _make_streaming_body(data: bytes, chunk_size: int = 16 * 1024) -> StreamingBody:
    """Build a StreamingBody whose underlying stream yields data in chunks."""
    reader = asyncio.StreamReader()
    for i in range(0, len(data), chunk_size):
        reader.feed_data(data[i : i + chunk_size])
    reader.feed_eof()
    raw = SimpleNamespace(content=reader, url="https://example.com/object")
    return StreamingBody(raw, str(len(data)))  # pyright: ignore[reportArgumentType]


class _ChunkedReader:
    """Stream reader that returns at most one chunk per read call."""

    def __init__(self, data: bytes, chunk_size: int) -> None:
        self._data = data
        self._chunk_size = chunk_size
        self._pos = 0

    async def read(self, n: int = -1) -> bytes:
        size = self._chunk_size if n < 0 else min(n, self._chunk_size)
        chunk = self._data[self._pos : self._pos + size]
        self._pos += len(chunk)
        return chunk


def _make_chunked_body(data: bytes, chunk_size: int) -> StreamingBody:
    raw = SimpleNamespace(
        content=_ChunkedReader(data, chunk_size), url="https://example.com/object"
    )
    return StreamingBody(raw, str(len(data)))  # pyright: ignore[reportArgumentType]


@pytest.mark.anyio
async def test_read_streaming_values_reads_full_text_body_across_chunks() -> None:
    data = b"x" * (1024 * 1024 + 123)
    body = _make_chunked_body(data, chunk_size=16 * 1024)

    result = await aws_boto3._read_streaming_values({"Body": body, "ETag": "abc"})

    assert result == {"Body": data.decode("utf-8"), "ETag": "abc"}


@pytest.mark.anyio
async def test_read_streaming_values_reads_full_binary_body_across_chunks() -> None:
    data = bytes(range(256)) * 512
    body = _make_streaming_body(data)

    result = await aws_boto3._read_streaming_values(body)

    assert base64.b64decode(result) == data


@pytest.mark.anyio
async def test_read_streaming_body_raises_when_exceeding_max_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(aws_boto3, "_STREAMING_BODY_MAX_BYTES", 32 * 1024)
    body = _make_chunked_body(b"x" * (32 * 1024 + 1), chunk_size=16 * 1024)

    with pytest.raises(ValueError, match="exceeds the maximum supported size"):
        await aws_boto3._read_streaming_body(body)


@pytest.mark.anyio
async def test_read_streaming_body_allows_exactly_max_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(aws_boto3, "_STREAMING_BODY_MAX_BYTES", 32 * 1024)
    data = b"x" * (32 * 1024)
    body = _make_chunked_body(data, chunk_size=16 * 1024)

    assert await aws_boto3._read_streaming_body(body) == data
