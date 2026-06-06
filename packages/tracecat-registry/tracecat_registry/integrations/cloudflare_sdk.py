"""Generic interface for Cloudflare Python SDK."""

from typing import Annotated, Any, cast

from cloudflare import Cloudflare
from cloudflare._base_client import BaseSyncPage
from pydantic import Field
from pydantic_core import to_jsonable_python

from tracecat_registry import RegistrySecret, registry, secrets

cloudflare_secret = RegistrySecret(
    name="cloudflare",
    keys=["CLOUDFLARE_API_TOKEN"],
)
"""Cloudflare API token.

- name: `cloudflare`
- keys:
    - `CLOUDFLARE_API_TOKEN`
"""


def _validate_public_name(name: str, field: str) -> None:
    if not name:
        raise ValueError(f"{field} cannot be empty.")
    if name.startswith("_"):
        raise ValueError(f"{field} cannot start with `_`.")


def _resolve_resource(client: Cloudflare, resource: str | None) -> Any:
    target: Any = client
    if resource is None:
        return target
    if not resource:
        raise ValueError("Resource path cannot be empty.")
    for part in resource.split("."):
        _validate_public_name(part, "Resource path segment")
        target = getattr(target, part)
    return target


@registry.register(
    default_title="Call method",
    description="Instantiate a Cloudflare client and call a Cloudflare SDK method.",
    display_group="Cloudflare SDK",
    doc_url="https://github.com/cloudflare/cloudflare-python",
    namespace="tools.cloudflare_sdk",
    secrets=[cloudflare_secret],
)
def call_method(
    method_name: Annotated[
        str,
        Field(..., description="Cloudflare SDK method name, e.g. `list` or `create`."),
    ],
    resource: Annotated[
        str | None,
        Field(
            ...,
            description="Cloudflare SDK resource path, e.g. `zones` or `dns.records`.",
        ),
    ] = None,
    params: Annotated[
        dict[str, Any] | None,
        Field(..., description="Parameters for the Cloudflare SDK method."),
    ] = None,
) -> Any:
    params = params or {}
    _validate_public_name(method_name, "Method name")
    client = Cloudflare(api_token=secrets.get("CLOUDFLARE_API_TOKEN"))
    method = getattr(_resolve_resource(client, resource), method_name)
    result = method(**params)
    if isinstance(result, BaseSyncPage):
        raise ValueError(
            "Cloudflare paginated responses must be called with "
            "`tools.cloudflare_sdk.call_paginated_method`."
        )
    return cast(Any, to_jsonable_python(result))


@registry.register(
    default_title="Call paginated method",
    description="Instantiate a Cloudflare client and call a paginated Cloudflare SDK method.",
    display_group="Cloudflare SDK",
    doc_url="https://github.com/cloudflare/cloudflare-python",
    namespace="tools.cloudflare_sdk",
    secrets=[cloudflare_secret],
)
def call_paginated_method(
    method_name: Annotated[
        str,
        Field(..., description="Paginated Cloudflare SDK method name, e.g. `list`."),
    ],
    resource: Annotated[
        str | None,
        Field(
            ...,
            description="Cloudflare SDK resource path, e.g. `zones` or `dns.records`.",
        ),
    ] = None,
    params: Annotated[
        dict[str, Any] | None,
        Field(..., description="Parameters for the Cloudflare SDK method."),
    ] = None,
) -> list[Any]:
    params = params or {}
    _validate_public_name(method_name, "Method name")
    client = Cloudflare(api_token=secrets.get("CLOUDFLARE_API_TOKEN"))
    method = getattr(_resolve_resource(client, resource), method_name)
    result = method(**params)
    if not isinstance(result, BaseSyncPage):
        raise ValueError(
            "Cloudflare paginated methods must return a Cloudflare page object."
        )
    # Iterating a Cloudflare page auto-paginates across all pages.
    return [to_jsonable_python(item) for item in result]
