"""Generic interface for the Databricks SDK for Python."""

import base64
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Annotated, Any, Protocol, runtime_checkable

from databricks.sdk import WorkspaceClient
from databricks.sdk.config import Config
from databricks.sdk.credentials_provider import (
    CredentialsProvider,
    CredentialsStrategy,
)
from databricks.sdk.service._internal import Wait
from pydantic import Field

from tracecat_registry import (
    RegistryOAuthSecret,
    RegistrySecret,
    registry,
    secrets,
)
from tracecat_registry.config import TRACECAT__MAX_FILE_SIZE_BYTES

databricks_user_oauth_secret = RegistryOAuthSecret(
    provider_id="databricks",
    grant_type="authorization_code",
    optional=True,
)
"""Databricks user OAuth credentials."""

databricks_service_oauth_secret = RegistryOAuthSecret(
    provider_id="databricks",
    grant_type="client_credentials",
    optional=True,
)
"""Databricks service principal OAuth credentials."""

databricks_secret = RegistrySecret(
    name="databricks",
    keys=[
        "DATABRICKS_CLIENT_ID",
        "DATABRICKS_CLIENT_SECRET",
    ],
    optional=True,
)
"""Stored Databricks OAuth M2M service credentials.

- name: `databricks`
- keys:
    - `DATABRICKS_CLIENT_ID` (OAuth M2M service principal)
    - `DATABRICKS_CLIENT_SECRET` (OAuth M2M service principal)

As an alternative to a Databricks OAuth provider, configure both keys. The
wrapper passes credentials explicitly so the SDK cannot discover ambient host
credentials.
"""


@dataclass(frozen=True, slots=True)
class _OAuthAccessTokenCredentials(CredentialsStrategy):
    token: str

    def auth_type(self) -> str:
        return "oauth-access-token"

    def __call__(self, _: Config) -> CredentialsProvider:
        def headers() -> dict[str, str]:
            return {"Authorization": f"Bearer {self.token}"}

        return headers


@runtime_checkable
class _BinaryStream(Protocol):
    def read(self, size: int = -1, /) -> bytes: ...


def _get_client(base_url: str) -> WorkspaceClient:
    if token := (
        secrets.get_or_default(databricks_user_oauth_secret.token_name)
        or secrets.get_or_default(databricks_service_oauth_secret.token_name)
    ):
        return WorkspaceClient(
            host=base_url,
            credentials_strategy=_OAuthAccessTokenCredentials(token),
        )
    return WorkspaceClient(
        host=base_url,
        client_id=secrets.get("DATABRICKS_CLIENT_ID"),
        client_secret=secrets.get("DATABRICKS_CLIENT_SECRET"),
        auth_type="oauth-m2m",
    )


def _get_sdk_method(client: WorkspaceClient, service: str, method_name: str) -> Any:
    """Resolve a method from a generated Workspace API service."""
    if sdk_service := getattr(client, service, None):
        if method := getattr(sdk_service, method_name, None):
            return method
    raise AttributeError(f"Unknown Databricks SDK method: {service}.{method_name}")


def _serialize(value: Any) -> Any:
    """Adapt Databricks SDK values into JSON-serializable values."""
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, bytes | bytearray | memoryview):
        if len(value) > TRACECAT__MAX_FILE_SIZE_BYTES:
            raise ValueError(
                "Databricks SDK binary response exceeds maximum size limit of "
                f"{TRACECAT__MAX_FILE_SIZE_BYTES // 1024 // 1024}MB"
            )
        return {
            "content_base64": base64.b64encode(bytes(value)).decode("ascii"),
        }
    if isinstance(value, _BinaryStream):
        return _serialize(value.read(TRACECAT__MAX_FILE_SIZE_BYTES + 1))
    if isinstance(value, Enum):
        return _serialize(value.value)
    if isinstance(value, Wait):
        bindings = value.bind()
        return {
            "response": _serialize(value.response),
            "bind": {str(key): _serialize(item) for key, item in bindings.items()},
        }
    if isinstance(value, Mapping):
        return {str(key): _serialize(item) for key, item in value.items()}
    if isinstance(value, Sequence):
        return [_serialize(item) for item in value]
    if isinstance(value, Iterator):
        raise TypeError(
            "Paginated SDK iterators must use tools.databricks_sdk.call_paginated_method"
        )
    if as_dict := getattr(value, "as_dict", None):
        return _serialize(as_dict())
    return value


@registry.register(
    default_title="Call method",
    description="Instantiate a Databricks workspace client and call an SDK method.",
    display_group="Databricks SDK",
    doc_url="https://databricks-sdk-py.readthedocs.io/en/latest/workspace/",
    namespace="tools.databricks_sdk",
    secrets=[
        databricks_user_oauth_secret,
        databricks_service_oauth_secret,
        databricks_secret,
    ],
)
def call_method(
    base_url: Annotated[
        str,
        Field(..., description="Databricks workspace URL."),
    ],
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
    method = _get_sdk_method(_get_client(base_url), service, method_name)
    return _serialize(method(**(params or {})))


@registry.register(
    default_title="Call paginated method",
    description=(
        "Instantiate a Databricks workspace client and collect a bounded number "
        "of items from an SDK iterator method."
    ),
    display_group="Databricks SDK",
    doc_url="https://github.com/databricks/databricks-sdk-py#paginated-responses",
    namespace="tools.databricks_sdk",
    secrets=[
        databricks_user_oauth_secret,
        databricks_service_oauth_secret,
        databricks_secret,
    ],
)
def call_paginated_method(
    base_url: Annotated[
        str,
        Field(..., description="Databricks workspace URL."),
    ],
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
    method = _get_sdk_method(_get_client(base_url), service, method_name)
    result = method(**(params or {}))

    items: list[Any] = []
    for item in result:
        items.append(_serialize(item))
        if len(items) >= limit:
            break
    return items
