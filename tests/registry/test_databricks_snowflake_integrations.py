"""Regression tests for Databricks and Snowflake integrations."""

import pytest
from databricks.sdk.service._internal import Wait
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
