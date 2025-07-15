from typing import Annotated
import httpx
from pydantic import Field
from tracecat_registry import RegistrySecret, registry, secrets

misp_secret = RegistrySecret(name="misp_api", keys=["MISP_API_KEY"])

@registry.register(
    default_title="Search IOC in MISP",
    description="Query MISP for a given IOC (IP, domain, hash, etc.) and check if it matches any known attributes.",
    display_group="MISP",
    doc_url="https://www.misp-project.org/openapi/#tag/Attributes/operation/restSearchAttributes",
    namespace="tools.misp",
    secrets=[misp_secret],
)
async def search_ioc_in_misp(
    base_url: Annotated[str, Field(..., description="Base URL for the MISP instance (e.g., https://misp.local)")],
    ioc_value: Annotated[str, Field(..., description="The IOC value to search for (e.g., IP, domain, hash).")],
    verify_ssl: Annotated[bool, Field(True, description="If False, disables SSL verification.")],
    return_format: Annotated[str, Field("json", description="Return format (json, xml, csv, etc.)")] = "json",
    include_event_uuid: Annotated[bool, Field(True, description="Include event UUID in results")] = True,
    include_event_tags: Annotated[bool, Field(True, description="Include event tags in results")] = True,
) -> dict:
    headers = {
        "Authorization": secrets.get("MISP_API_KEY"),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    url = f"{base_url.rstrip('/')}/attributes/restSearch"
    params = {
        "value": ioc_value,
        "returnFormat": return_format,
        "includeEventUuid": str(include_event_uuid).lower(),
        "includeEventTags": str(include_event_tags).lower()
    }

    async with httpx.AsyncClient(verify=verify_ssl) as client:
        response = await client.post(url, headers=headers, params=params)
        response.raise_for_status()
        return response.json()
