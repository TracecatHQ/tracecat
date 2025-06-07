from typing import Annotated
import httpx
from pydantic import Field
from tracecat_registry import RegistrySecret, registry, secrets

misp_secret = RegistrySecret(name="misp_api", keys=["MISP_API_KEY"])

async def _post_to_misp(
    base_url: str,
    path: str,
    payload: dict,
    verify_ssl: bool,
) -> dict:
    """Generic helper to send POST requests to a MISP endpoint."""
    headers = {
        "Authorization": secrets.get("MISP_API_KEY"),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    async with httpx.AsyncClient(verify=verify_ssl) as client:
        response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        return response.json()

@registry.register(
    default_title="Search IOC in MISP",
    description="Query MISP for a given IOC (IP, domain, hash, etc.) and check if it matches any known attributes.",
    display_group="MISP",
    doc_url="https://www.misp-project.org/openapi/#tag/Events/operation/searchEvents",
    namespace="tools.misp",
    secrets=[misp_secret],
)
async def search_ioc_in_misp(
    base_url: Annotated[str, Field(..., description="Base URL for the MISP instance (e.g., https://misp.local)")],
    ioc_value: Annotated[str, Field(..., description="The IOC value to search for (e.g., IP, domain, hash).")],
    verify_ssl: Annotated[bool, Field(True, description="If False, disables SSL verification.")],
) -> dict:
    payload = {
        "value": ioc_value,
        "returnFormat": "json"
    }
    return await _post_to_misp(base_url, "attributes/restSearch", payload, verify_ssl)
