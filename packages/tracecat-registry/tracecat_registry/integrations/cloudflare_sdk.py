"""Generic interface for Cloudflare Python SDK."""

from typing import Annotated, Any, cast

from cloudflare import Cloudflare
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


def _resolve_resource(client: Cloudflare, resource: str | None) -> Any:
    target: Any = client
    if not resource:
        return target
    for part in resource.split("."):
        if not part:
            raise ValueError("Resource path cannot contain empty segments.")
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
            description="Cloudflare SDK resource path, e.g. `zones` or `zones.dns.records`.",
        ),
    ] = None,
    params: Annotated[
        dict[str, Any] | None,
        Field(..., description="Parameters for the Cloudflare SDK method."),
    ] = None,
) -> Any:
    params = params or {}
    client = Cloudflare(api_token=secrets.get("CLOUDFLARE_API_TOKEN"))
    method = getattr(_resolve_resource(client, resource), method_name)
    result = method(**params)
    return cast(Any, to_jsonable_python(result))
