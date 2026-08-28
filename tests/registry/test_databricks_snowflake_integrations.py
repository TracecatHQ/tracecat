"""Regression tests for Databricks and Snowflake integrations."""

from io import BytesIO

import pytest
from databricks.sdk.service._internal import Wait
from databricks.sdk.service.files import DownloadResponse
from tracecat_registry.integrations import databricks_sdk
from tracecat_registry.integrations.databricks_sdk import _serialize


def test_databricks_serializes_waiter_response_and_bindings() -> None:
    waiter = Wait(
        lambda **_kwargs: None,
        response={"run_id": 123},
        run_id=123,
        nested={"state": "PENDING"},
    )

    assert _serialize(waiter) == {
        "response": {"run_id": 123},
        "bind": {"run_id": 123, "nested": {"state": "PENDING"}},
    }


def test_databricks_direct_dispatch_rejects_paginated_iterators() -> None:
    with pytest.raises(TypeError, match="call_paginated_method"):
        _serialize(iter([{"cluster_id": "cluster-123"}]))


def test_databricks_serializes_direct_binary_stream() -> None:
    assert _serialize(BytesIO(b"\x89PNG\r\n")) == {
        "content_base64": "iVBORw0K",
    }


def test_databricks_serializes_binary_sdk_response() -> None:
    response = DownloadResponse(
        content_length=6,
        content_type="image/png",
        contents=BytesIO(b"\x89PNG\r\n"),
    )

    assert _serialize(response) == {
        "content-length": 6,
        "content-type": "image/png",
        "contents": {"content_base64": "iVBORw0K"},
    }


def test_databricks_bounds_binary_stream_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(databricks_sdk, "TRACECAT__MAX_FILE_SIZE_BYTES", 4)
    stream = BytesIO(b"12345remaining-content-is-not-read")

    with pytest.raises(ValueError, match="binary response exceeds maximum size"):
        _serialize(stream)

    assert stream.tell() == 5
