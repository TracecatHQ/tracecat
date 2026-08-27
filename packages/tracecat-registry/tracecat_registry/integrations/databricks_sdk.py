"""Generic interface for the Databricks SDK for Python."""

from collections.abc import Mapping, Sequence
from enum import Enum
from typing import Annotated, Any, cast

from databricks.sdk import WorkspaceClient
from databricks.sdk.service._internal import Wait
from pydantic import Field

from tracecat_registry import (
    RegistrySecret,
    registry,
    secrets,
)

databricks_secret = RegistrySecret(
    name="databricks",
    keys=["DATABRICKS_HOST"],
    optional_keys=[
        "DATABRICKS_TOKEN",
        "DATABRICKS_CLIENT_ID",
        "DATABRICKS_CLIENT_SECRET",
    ],
)
"""Databricks workspace credentials.

- name: `databricks`
- keys:
    - `DATABRICKS_HOST`
- optional_keys:
    - `DATABRICKS_TOKEN` (personal access token)
    - `DATABRICKS_CLIENT_ID` (OAuth M2M service principal)
    - `DATABRICKS_CLIENT_SECRET` (OAuth M2M service principal)

Configure either `DATABRICKS_TOKEN` or both OAuth M2M keys. The wrapper passes
credentials explicitly so the SDK cannot discover ambient host credentials.
"""


def _get_client() -> WorkspaceClient:
    host = secrets.get("DATABRICKS_HOST")
    token = secrets.get_or_default("DATABRICKS_TOKEN")
    if token:
        return WorkspaceClient(host=host, token=token, auth_type="pat")
    return WorkspaceClient(
        host=host,
        client_id=secrets.get_or_default("DATABRICKS_CLIENT_ID"),
        client_secret=secrets.get_or_default("DATABRICKS_CLIENT_SECRET"),
        auth_type="oauth-m2m",
    )


def _serialize(value: Any) -> Any:
    """Adapt Databricks SDK values into JSON-serializable values."""
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Enum):
        return _serialize(value.value)
    if isinstance(value, Wait):
        return {
            "response": _serialize(value.response),
            "bind": _serialize(value.bind()),
        }
    if isinstance(value, Mapping):
        return {str(key): _serialize(item) for key, item in value.items()}
    if isinstance(value, Sequence):
        return [_serialize(item) for item in value]
    if as_dict := getattr(value, "as_dict", None):
        return _serialize(as_dict())
    return value


def _resolve_method(client: WorkspaceClient, service: str, method_name: str) -> Any:
    return getattr(getattr(client, service), method_name)


@registry.register(
    default_title="Call method",
    description="Instantiate a Databricks workspace client and call an SDK method.",
    display_group="Databricks SDK",
    doc_url="https://databricks-sdk-py.readthedocs.io/en/latest/workspace/",
    namespace="tools.databricks_sdk",
    secrets=[databricks_secret],
)
def call_method(
    service: Annotated[
        str,
        Field(
            ...,
            description=(
                "WorkspaceClient service name, for example `clusters`, `jobs`, "
                "`warehouses`, or `statement_execution`."
            ),
        ),
    ],
    method_name: Annotated[
        str,
        Field(..., description="Public Databricks SDK method name."),
    ],
    params: Annotated[
        dict[str, Any] | None,
        Field(
            ...,
            description=(
                "Databricks SDK method parameters. Values, including nulls, are "
                "forwarded unchanged."
            ),
        ),
    ] = None,
) -> Any:
    method = _resolve_method(_get_client(), service, method_name)
    return cast(Any, _serialize(method(**(params or {}))))


@registry.register(
    default_title="Call paginated method",
    description=(
        "Instantiate a Databricks workspace client and collect a bounded number "
        "of items from an SDK iterator method."
    ),
    display_group="Databricks SDK",
    doc_url="https://github.com/databricks/databricks-sdk-py#paginated-responses",
    namespace="tools.databricks_sdk",
    secrets=[databricks_secret],
)
def call_paginated_method(
    service: Annotated[
        str,
        Field(..., description="WorkspaceClient service name."),
    ],
    method_name: Annotated[
        str,
        Field(..., description="Public Databricks SDK iterator method name."),
    ],
    params: Annotated[
        dict[str, Any] | None,
        Field(
            ...,
            description=(
                "Databricks SDK method parameters. Values, including nulls, are "
                "forwarded unchanged."
            ),
        ),
    ] = None,
    limit: Annotated[
        int,
        Field(
            ...,
            ge=1,
            description="Maximum number of items to return across SDK pages.",
        ),
    ] = 1000,
) -> list[Any]:
    method = _resolve_method(_get_client(), service, method_name)
    result = method(**(params or {}))

    items: list[Any] = []
    for item in result:
        items.append(_serialize(item))
        if len(items) >= limit:
            break
    return items
