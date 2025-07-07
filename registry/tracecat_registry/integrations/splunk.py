from typing import Annotated, Any, Literal

import httpx
from pydantic import Field

from tracecat_registry import RegistrySecret, registry, secrets

splunk_secret = RegistrySecret(
    name="splunk",
    keys=["SPLUNK_API_KEY"],
)
"""Splunk API Key.

- name: `splunk`
- keys:
    - `SPLUNK_API_KEY` (API key)

Note: `SPLUNK_API_KEY` should be a valid API key for the Splunk instance.
"""

# TODO: Migrate Create KV Store Collections Template to this file.
# TODO: Migrate Create Entry to KV Store Collections Template to this file.
# TODO: Migrate Get KV Store Collections Template to this file.
# TODO: Migrate List KV Store Collections Template to this file.
# TODO: Migrate List Entries from KV Store Collections Template to this file.
# TODO: Migrate Edit KV Store Collections Template to this file.
# TODO: Migrate Delete KV Store Collections Template to this file.
# TODO: Migrate Delete Entry from KV Store Collections Template to this file.


@registry.register(
    default_title="Add fields to KV Store Collections",
    description="Add fields to KV Store Collections from Splunk. Uses bearer token authentication.",
    display_group="Splunk",
    doc_url="https://help.splunk.com/en/splunk-enterprise/rest-api-reference/9.4/kv-store-endpoints/kv-store-endpoint-descriptions#post-9",
    namespace="tools.splunk_sdk",
    secrets=[splunk_secret],
)
async def add_fields_to_kv_store_collections(
    base_url: Annotated[
        str,
        Field(
            ...,
            description="Splunk base URL (e.g. https://localhost:8089 or https://tracecat.splunkcloud.com:8089).",
        ),
    ],
    name: Annotated[
        str,
        Field(
            ...,
            description="Name of the KV Store Collection. Must be unique and contain only alphanumeric characters, underscores, and hyphens.",
        ),
    ],
    fields: Annotated[
        list[dict[Literal["name", "type"], str]],
        Field(
            ...,
            description="List of fields to add. Should contain a field name and type. Type options are array, number, bool, string, cidr, time.",
        ),
    ],
    verify_ssl: Annotated[
        bool, Field(..., description="Whether to verify SSL certificates.")
    ] = True,
    owner: Annotated[
        str,
        Field(..., description="Owner of the KV Store Collection. Default is nobody."),
    ] = "nobody",
    app: Annotated[
        str, Field(..., description="ame of the app. Default is search.")
    ] = "search",
) -> dict[str, Any]:
    """Create a KV Store Collection in Splunk."""
    # Example Request: https://localhost:8089/servicesNS/nobody/search/storage/collections/config/test -d 'accelerated_fields.foo={"a": 1}' -d 'accelerated_fields.bar={"b": -1}' -d "field.a=number" -d "field.b=cidr"
    api_key = secrets.get("SPLUNK_API_KEY")
    url = f"{base_url}/servicesNS/{owner}/{app}/storage/collections/config/{name}"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    payload = {
        "output_mode": "json",
    }
    # Fields in Splunk are stored in field.name=type
    for field in fields:
        payload[f"field.{field['name']}"] = field["type"]

    async with httpx.AsyncClient(verify=verify_ssl) as client:
        response = await client.post(url, headers=headers, data=payload)
        response.raise_for_status()
        return response.json()
