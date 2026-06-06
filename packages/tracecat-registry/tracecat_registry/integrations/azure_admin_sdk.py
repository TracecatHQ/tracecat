"""Generic interface for Azure Management SDK clients."""

import importlib
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Any, cast

from azure.core.credentials import AccessToken
from azure.core.paging import ItemPaged
from azure.core.polling import LROPoller
from pydantic import Field
from pydantic_core import to_jsonable_python

from tracecat_registry import RegistryOAuthSecret, registry, secrets

azure_management_oauth_secret = RegistryOAuthSecret(
    provider_id="azure_management",
    grant_type="client_credentials",
)
"""Azure Management OAuth2.0 credentials (client credentials grant).

- provider_id: `azure_management`
- grant_type: `client_credentials`
- token_name: `AZURE_MANAGEMENT_SERVICE_TOKEN`
"""


class StaticTokenCredential:
    """Azure TokenCredential backed by a Tracecat-managed OAuth service token."""

    def __init__(self, token: str) -> None:
        self._token = token

    def get_token(self, *scopes: str, **kwargs: Any) -> AccessToken:
        return AccessToken(self._token, int(time.time()) + 3600)


def _validate_public_name(name: str, field: str) -> None:
    if not name:
        raise ValueError(f"{field} cannot be empty.")
    if name.startswith("_"):
        raise ValueError(f"{field} cannot start with `_`.")


def _load_client_class(module_name: str, client_class: str) -> type[Any]:
    if module_name != "azure.mgmt" and not module_name.startswith("azure.mgmt."):
        raise ValueError("Azure admin SDK module_name must start with `azure.mgmt`.")
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise ValueError(
            f"Azure management module `{module_name}` is not installed in this "
            "Tracecat deployment. Only the Azure management SDK packages bundled "
            "with the registry can be used."
        ) from exc
    cls = getattr(module, client_class, None)
    if not isinstance(cls, type):
        raise TypeError(f"{module_name}.{client_class} is not a class.")
    return cls


# Client kwargs that could redirect the bearer token to an attacker-controlled
# endpoint or override the Tracecat-managed credential. Blocked to prevent SSRF
# and OAuth token exfiltration.
_BLOCKED_CLIENT_KWARGS = frozenset(
    {"base_url", "endpoint", "credential", "credentials", "credential_scopes"}
)


def _validate_client_kwargs(client_kwargs: dict[str, Any]) -> None:
    blocked = sorted(_BLOCKED_CLIENT_KWARGS & client_kwargs.keys())
    if blocked:
        raise ValueError(
            "client_kwargs may not override credential or endpoint settings: "
            f"{', '.join(blocked)}."
        )


def _resolve_method(client: Any, method_path: str | None, method_name: str) -> Any:
    target = client
    if method_path is not None:
        if not method_path:
            raise ValueError("Method path cannot be empty.")
        for part in method_path.split("."):
            _validate_public_name(part, "Method path segment")
            target = getattr(target, part)
    _validate_public_name(method_name, "Method name")
    return getattr(target, method_name)


def _to_jsonable(obj: Any) -> Any:
    """Serialize a single Azure SDK value. Azure models expose `as_dict()`."""
    if as_dict := getattr(obj, "as_dict", None):
        return cast(Any, as_dict())
    return cast(Any, to_jsonable_python(obj))


def _serialize_result(result: Any) -> Any:
    if isinstance(result, ItemPaged):
        raise ValueError(
            "Azure paginated responses must be called with "
            "`tools.azure_admin_sdk.call_paginated_method`."
        )
    if isinstance(result, LROPoller):
        # `begin_*` methods return a long-running poller; wait for the operation
        # to finish so the action returns a serializable result, not the poller.
        result = result.result()
    return _to_jsonable(result)


@contextmanager
def _management_client(
    module_name: str,
    client_class: str,
    subscription_id: str,
    client_kwargs: dict[str, Any] | None,
) -> Iterator[Any]:
    """Build an isolated Azure management client and close it on exit.

    The client must stay open while pagers are iterated and pollers are
    resolved, so serialization happens inside the `with` block.
    """
    client_kwargs = client_kwargs or {}
    _validate_client_kwargs(client_kwargs)
    token = secrets.get(azure_management_oauth_secret.token_name)
    client_type = _load_client_class(module_name, client_class)
    client = client_type(
        credential=StaticTokenCredential(token),
        subscription_id=subscription_id,
        **client_kwargs,
    )
    try:
        yield client
    finally:
        if close := getattr(client, "close", None):
            close()


@registry.register(
    default_title="Call method",
    description="Instantiate an Azure Management SDK client and call a method.",
    display_group="Azure Admin SDK",
    doc_url="https://learn.microsoft.com/en-us/python/api/overview/azure/mgmt",
    namespace="tools.azure_admin_sdk",
    secrets=[azure_management_oauth_secret],
)
def call_method(
    module_name: Annotated[
        str,
        Field(
            ...,
            description="Azure management SDK module, e.g. `azure.mgmt.resource`.",
        ),
    ],
    client_class: Annotated[
        str,
        Field(..., description="Azure management SDK client class name."),
    ],
    method_name: Annotated[
        str,
        Field(..., description="Method name to call on the client or method path."),
    ],
    subscription_id: Annotated[
        str,
        Field(..., description="Azure subscription ID for the management client."),
    ],
    method_path: Annotated[
        str | None,
        Field(
            ...,
            description="Optional dotted path on the client, e.g. `resource_groups`.",
        ),
    ] = None,
    params: Annotated[
        dict[str, Any] | None,
        Field(..., description="Parameters for the Azure SDK method."),
    ] = None,
    client_kwargs: Annotated[
        dict[str, Any] | None,
        Field(
            ...,
            description=(
                "Extra keyword arguments for the Azure SDK client. Credential and "
                "endpoint overrides (e.g. `base_url`, `endpoint`) are not allowed."
            ),
        ),
    ] = None,
) -> Any:
    with _management_client(
        module_name, client_class, subscription_id, client_kwargs
    ) as client:
        result = _resolve_method(client, method_path, method_name)(**(params or {}))
        return _serialize_result(result)


@registry.register(
    default_title="Call paginated method",
    description="Instantiate an Azure Management SDK client and call a paginated method.",
    display_group="Azure Admin SDK",
    doc_url="https://learn.microsoft.com/en-us/python/api/overview/azure/mgmt",
    namespace="tools.azure_admin_sdk",
    secrets=[azure_management_oauth_secret],
)
def call_paginated_method(
    module_name: Annotated[
        str,
        Field(
            ...,
            description="Azure management SDK module, e.g. `azure.mgmt.resource`.",
        ),
    ],
    client_class: Annotated[
        str,
        Field(..., description="Azure management SDK client class name."),
    ],
    method_name: Annotated[
        str,
        Field(
            ...,
            description="Paginated method name to call on the client or method path.",
        ),
    ],
    subscription_id: Annotated[
        str,
        Field(..., description="Azure subscription ID for the management client."),
    ],
    method_path: Annotated[
        str | None,
        Field(
            ...,
            description="Optional dotted path on the client, e.g. `resource_groups`.",
        ),
    ] = None,
    params: Annotated[
        dict[str, Any] | None,
        Field(..., description="Parameters for the Azure SDK method."),
    ] = None,
    client_kwargs: Annotated[
        dict[str, Any] | None,
        Field(
            ...,
            description=(
                "Extra keyword arguments for the Azure SDK client. Credential and "
                "endpoint overrides (e.g. `base_url`, `endpoint`) are not allowed."
            ),
        ),
    ] = None,
) -> list[Any]:
    with _management_client(
        module_name, client_class, subscription_id, client_kwargs
    ) as client:
        result = _resolve_method(client, method_path, method_name)(**(params or {}))
        if not isinstance(result, ItemPaged):
            raise ValueError(
                "Azure paginated methods must return an Azure ItemPaged object."
            )
        return [_to_jsonable(item) for item in result]
