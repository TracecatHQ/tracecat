"""Generic interface for Azure Management SDK clients."""

import importlib
import time
from typing import Annotated, Any, cast

from azure.core.credentials import AccessToken
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


def _load_client_class(module_name: str, client_class: str) -> type[Any]:
    if module_name != "azure.mgmt" and not module_name.startswith("azure.mgmt."):
        raise ValueError("Azure admin SDK module_name must start with `azure.mgmt`.")
    module = importlib.import_module(module_name)
    cls = getattr(module, client_class)
    if not isinstance(cls, type):
        raise TypeError(f"{module_name}.{client_class} is not a class.")
    return cls


def _resolve_method(client: Any, method_path: str | None, method_name: str) -> Any:
    target = client
    if method_path:
        for part in method_path.split("."):
            if not part:
                raise ValueError("Method path cannot contain empty segments.")
            target = getattr(target, part)
    return getattr(target, method_name)


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
        Field(..., description="Extra keyword arguments for the Azure SDK client."),
    ] = None,
) -> Any:
    params = params or {}
    client_kwargs = client_kwargs or {}
    token = secrets.get(azure_management_oauth_secret.token_name)
    client_type = _load_client_class(module_name, client_class)
    client = client_type(
        credential=StaticTokenCredential(token),
        subscription_id=subscription_id,
        **client_kwargs,
    )
    result = _resolve_method(client, method_path, method_name)(**params)
    return cast(Any, to_jsonable_python(result))
