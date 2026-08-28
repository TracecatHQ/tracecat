"""Regression tests for Databricks and Snowflake integrations."""

from io import BytesIO

import pytest
from databricks.sdk import WorkspaceClient
from databricks.sdk.service._internal import Wait
from databricks.sdk.service.files import DownloadResponse
from tracecat_registry.integrations import databricks_sdk
from tracecat_registry.integrations.databricks_sdk import _get_sdk_method, _serialize


@pytest.mark.parametrize(
    ("service", "method_name"),
    [
        ("_api_client", "do"),
        ("clusters", "_api"),
    ],
)
def test_databricks_dispatch_rejects_private_attributes(
    service: str,
    method_name: str,
) -> None:
    # The private-name boundary runs before SDK state is accessed. Allocate the
    # pinned client type without initialization so the regression cannot perform
    # OAuth discovery or any network request.
    client = object.__new__(WorkspaceClient)

    with pytest.raises(AttributeError, match="Unknown Databricks SDK method"):
        _get_sdk_method(client, service, method_name)


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
