"""Generic interface for Cloudflare Python SDK."""

from collections.abc import Iterable
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


def _serialize_result(result: Any) -> Any:
    if isinstance(result, BaseSyncPage):
        raise ValueError(
            "Cloudflare paginated responses must be called with "
            "`tools.cloudflare_sdk.call_paginated_method`."
        )
    if isinstance(result, dict):
        return {key: _serialize_result(value) for key, value in result.items()}
    if isinstance(result, list):
        return [_serialize_result(item) for item in result]
    if isinstance(result, tuple | set):
        return [_serialize_result(item) for item in result]
    if to_dict := getattr(result, "to_dict", None):
        return _serialize_result(to_dict())
    if model_dump := getattr(result, "model_dump", None):
        return _serialize_result(model_dump(mode="json"))
    return cast(Any, to_jsonable_python(result))


def _flatten_page_items(page: BaseSyncPage[Any]) -> list[Any]:
    items = page._get_page_items()
    if not isinstance(items, Iterable):
        raise ValueError("Cloudflare page items must be iterable.")
    return [_serialize_result(item) for item in items]


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
            description="Cloudflare SDK resource path, e.g. `zones` or `zones.dns.records`.",
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
    return _serialize_result(result)


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
            description="Cloudflare SDK resource path, e.g. `zones` or `zones.dns.records`.",
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

    items: list[Any] = []
    for page in result.iter_pages():
        items.extend(_flatten_page_items(page))
    return items
