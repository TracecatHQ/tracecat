"""Generic interface for the Databricks SDK for Python."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Annotated, Any
from urllib.parse import urlsplit

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

_DATABRICKS_HOST_SUFFIXES = ("databricks.com", "azuredatabricks.net")
_UNSAFE_WORKSPACE_CLIENT_PROPERTIES = frozenset(
    {
        "api_client",
        "config",
        # These SDK mixins accept local paths and can read, overwrite, or delete
        # files on the executor rather than only calling workspace APIs.
        "dbfs",
        "files",
    }
)
_WORKSPACE_SERVICE_NAMES = frozenset(
    name
    for name, attribute in vars(WorkspaceClient).items()
    if isinstance(attribute, property)
    and name not in _UNSAFE_WORKSPACE_CLIENT_PROPERTIES
)

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
    keys=["DATABRICKS_HOST"],
    optional_keys=[
        "DATABRICKS_CLIENT_ID",
        "DATABRICKS_CLIENT_SECRET",
    ],
)
"""Databricks workspace credentials.

- name: `databricks`
- keys:
    - `DATABRICKS_HOST`
- optional_keys:
    - `DATABRICKS_CLIENT_ID` (OAuth M2M service principal)
    - `DATABRICKS_CLIENT_SECRET` (OAuth M2M service principal)

As an alternative to a Databricks OAuth provider, configure both OAuth M2M
keys. The wrapper passes credentials explicitly so the SDK cannot discover
ambient host credentials.
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


def _get_client() -> WorkspaceClient:
    host = secrets.get("DATABRICKS_HOST")
    _validate_databricks_host(host)
    if token := (
        secrets.get_or_default(databricks_user_oauth_secret.token_name)
        or secrets.get_or_default(databricks_service_oauth_secret.token_name)
    ):
        return WorkspaceClient(
            host=host,
            credentials_strategy=_OAuthAccessTokenCredentials(token),
        )
    return WorkspaceClient(
        host=host,
        client_id=secrets.get("DATABRICKS_CLIENT_ID"),
        client_secret=secrets.get("DATABRICKS_CLIENT_SECRET"),
        auth_type="oauth-m2m",
    )


def _validate_databricks_host(host: str) -> None:
    """Keep Databricks credentials bound to an official workspace domain."""
    parsed = urlsplit(host)
    hostname = parsed.hostname or ""
    if parsed.scheme != "https" or not any(
        hostname == suffix or hostname.endswith(f".{suffix}")
        for suffix in _DATABRICKS_HOST_SUFFIXES
    ):
        raise ValueError("DATABRICKS_HOST must be an HTTPS Databricks workspace URL")


def _get_sdk_method(client: WorkspaceClient, service: str, method_name: str) -> Any:
    """Resolve a public method from a generated Workspace API service."""
    if service not in _WORKSPACE_SERVICE_NAMES or method_name.startswith("_"):
        raise AttributeError(
            f"Unknown public Databricks SDK method: {service}.{method_name}"
        )
    return getattr(getattr(client, service), method_name)


def _serialize(value: Any) -> Any:
    """Adapt Databricks SDK values into JSON-serializable values."""
    if value is None or isinstance(value, str | int | float | bool):
        return value
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
    method = _get_sdk_method(_get_client(), service, method_name)
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
    method = _get_sdk_method(_get_client(), service, method_name)
    result = method(**(params or {}))

    items: list[Any] = []
    for item in result:
        items.append(_serialize(item))
        if len(items) >= limit:
            break
    return items
