import base64
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import falconpy.api_complete._advanced as falconpy_advanced
import pytest
import yaml
from falconpy import APIHarnessV2
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


TEMPLATES = (
    Path(__file__).resolve().parents[2]
    / "packages"
    / "tracecat-registry"
    / "tracecat_registry"
    / "templates"
    / "tools"
    / "crowdstrike"
)


def _falconpy_steps() -> list[tuple[str, str, dict[str, Any]]]:
    steps = []
    for path in sorted(TEMPLATES.glob("*.yml")):
        definition = yaml.safe_load(path.read_text())["definition"]
        for step in definition["steps"]:
            if step["action"] == "tools.falconpy.call_command":
                args = step["args"]
                steps.append((path.stem, args["operation_id"], args.get("params", {})))
    return steps


@pytest.mark.parametrize(
    ("operation_id", "params"),
    [
        pytest.param(op, params, id=f"{name}-{op}")
        for name, op, params in _falconpy_steps()
    ],
)
def test_template_params_build_falconpy_request(
    operation_id: str, params: dict[str, Any]
) -> None:
    requests: list[dict[str, Any]] = []

    def record(**kwargs: Any) -> dict[str, Any]:
        requests.append(kwargs)
        return {"status_code": 200, "headers": {}, "body": {}}

    kwargs = {key: {} if key == "body" else "x" for key in params}
    falcon = APIHarnessV2(access_token="token")
    with patch.object(falconpy_advanced, "perform_request", side_effect=record):
        falcon.command(operation_id, **kwargs)

    assert len(requests) == 1
    assert "{" not in requests[0]["endpoint"]


def test_get_search_status_builds_path() -> None:
    requests: list[dict[str, Any]] = []

    def record(**kwargs: Any) -> dict[str, Any]:
        requests.append(kwargs)
        return {"status_code": 200, "headers": {}, "body": {}}

    falcon = APIHarnessV2(access_token="token")
    with patch.object(falconpy_advanced, "perform_request", side_effect=record):
        falcon.command(
            "GetSearchStatusV1", repository="search-all", id="job-1", search_id="job-1"
        )

    assert requests[0]["endpoint"].endswith(
        "/humio/api/v1/repositories/search-all/queryjobs/job-1"
    )
    assert requests[0]["params"] == {}
