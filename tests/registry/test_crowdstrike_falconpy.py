import base64
from collections.abc import Iterator
from typing import Any

import pytest
from tracecat_registry.integrations import crowdstrike_falconpy
from tracecat_registry.integrations.crowdstrike_falconpy import (
    _encode_bytes,
    _read_stream,
)


class FakeStream:
    def __init__(self, chunks: list[bytes], status_code: int = 200) -> None:
        self.chunks = chunks
        self.status_code = status_code
        self.ok = status_code < 400
        self.headers = {"Content-Type": "application/x-7z-compressed"}
        self.text = '{"errors": [{"message": "not found"}]}'
        self.closed = False
        self.read = 0

    def json(self) -> Any:
        return {"errors": [{"message": "not found"}]}

    def iter_content(self, chunk_size: int) -> Iterator[bytes]:
        for chunk in self.chunks:
            self.read += 1
            yield chunk

    def close(self) -> None:
        self.closed = True


def test_encode_bytes_returns_content_base64() -> None:
    assert _encode_bytes(b"7z-archive") == {
        "content_base64": base64.b64encode(b"7z-archive").decode()
    }


def test_encode_bytes_rejects_over_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(crowdstrike_falconpy, "TRACECAT__MAX_FILE_SIZE_BYTES", 4)
    with pytest.raises(ValueError, match="maximum size"):
        _encode_bytes(b"12345")


def test_read_stream_encodes_content_and_closes() -> None:
    stream = FakeStream([b"ab", b"cd"])
    assert _read_stream(stream) == {
        "content_base64": base64.b64encode(b"abcd").decode()
    }
    assert stream.closed


def test_read_stream_stops_reading_over_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(crowdstrike_falconpy, "TRACECAT__MAX_FILE_SIZE_BYTES", 3)
    stream = FakeStream([b"ab", b"cd", b"ef"])
    with pytest.raises(ValueError, match="maximum size"):
        _read_stream(stream)
    assert stream.read == 2
    assert stream.closed


def test_read_stream_returns_error_body() -> None:
    stream = FakeStream([], status_code=404)
    result = _read_stream(stream)
    assert result["status_code"] == 404
    assert result["body"] == {"errors": [{"message": "not found"}]}
    assert stream.closed
