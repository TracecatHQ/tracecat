"""Regression tests for Databricks and Snowflake integrations."""

from io import BytesIO

import pytest
from databricks.sdk.service._internal import Wait
from databricks.sdk.service.files import DownloadResponse
from tracecat_registry.integrations import databricks_sdk
from tracecat_registry.integrations.databricks_sdk import (
    _WORKSPACE_SERVICE_NAMES,
    _serialize,
    _validate_databricks_host,
)
from tracecat_registry.integrations.snowflake_sql import _validate_snowflake_url


@pytest.mark.parametrize(
    "host",
    [
        "https://dbc-example.cloud.databricks.com",
        "https://example.gcp.databricks.com",
        "https://adb-example.azuredatabricks.net",
    ],
)
def test_databricks_workspace_hosts(host: str) -> None:
    _validate_databricks_host(host)


@pytest.mark.parametrize(
    "host",
    [
        "http://dbc-example.cloud.databricks.com",
        "https://databricks.example.com",
        "https://cloud.databricks.com.example.org",
    ],
)
def test_databricks_rejects_credential_exfiltration_hosts(host: str) -> None:
    with pytest.raises(ValueError, match="HTTPS Databricks workspace URL"):
        _validate_databricks_host(host)


def test_databricks_dispatch_excludes_client_internals() -> None:
    assert {"jobs", "clusters", "statement_execution"} <= _WORKSPACE_SERVICE_NAMES
    assert {"api_client", "config", "dbfs", "files"}.isdisjoint(
        _WORKSPACE_SERVICE_NAMES
    )


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


@pytest.mark.parametrize(
    "url",
    [
        "https://org-account.snowflakecomputing.com/api/v2/statements",
        "https://org-account.privatelink.snowflakecomputing.com/api/v2/statements",
        "https://org-account.snowflakecomputing.cn/api/v2/statements",
    ],
)
def test_snowflake_account_urls(url: str) -> None:
    _validate_snowflake_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://org-account.snowflakecomputing.com/api/v2/statements",
        "https://snowflakecomputing.example.com/api/v2/statements",
        "https://snowflakecomputing.com.example.org/api/v2/statements",
    ],
)
def test_snowflake_rejects_credential_exfiltration_urls(url: str) -> None:
    with pytest.raises(ValueError, match="HTTPS Snowflake account endpoint"):
        _validate_snowflake_url(url)
