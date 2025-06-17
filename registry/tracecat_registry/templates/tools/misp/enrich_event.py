from typing import Annotated, List
import httpx
from pydantic import Field
from tracecat_registry import RegistrySecret, registry, secrets

misp_secret = RegistrySecret(name="misp_api", keys=["MISP_API_KEY"])

@registry.register(
    default_title="Enrich MISP Event",
    description="Enrich an existing MISP event using selected enrichment modules.",
    display_group="MISP",
    doc_url="https://www.misp-project.org/openapi/#tag/Events/operation/enrichEvent",
    namespace="tools.misp",
    secrets=[misp_secret],
)
async def enrich_misp_event(
    base_url: Annotated[str, Field(..., description="Base URL of the MISP instance")],
    event_id: Annotated[int, Field(..., description="ID of the MISP event to enrich")],
    modules: Annotated[List[str], Field(..., description="List of enrichment module names (e.g. ['crowdsec', 'yara'])")],
    verify_ssl: Annotated[bool, Field(True)] = True,
    force: Annotated[bool, Field(False, description="Force enrichment even if already enriched")] = False,
) -> dict:
    headers = {
        "Authorization": secrets.get("MISP_API_KEY"),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    url = f"{base_url.rstrip('/')}/events/enrichEvent/{event_id}"
    params = {module: "true" for module in modules}
    if force:
        params["force"] = "true"

    async with httpx.AsyncClient(verify=verify_ssl) as client:
        response = await client.post(url, headers=headers, params=params)
        response.raise_for_status()
        return response.json()

@registry.register(
    default_title="Enrich MISP Attribute",
    description="Enrich a specific attribute in a MISP event using selected enrichment modules.",
    display_group="MISP",
    doc_url="https://www.misp-project.org/openapi/#tag/Attributes/operation/enrichAttribute",
    namespace="tools.misp",
    secrets=[misp_secret],
)
async def enrich_misp_attribute(
    base_url: Annotated[str, Field(..., description="Base URL of the MISP instance")],
    attribute_id: Annotated[int, Field(..., description="ID of the attribute to enrich")],
    modules: Annotated[List[str], Field(..., description="List of enrichment module names (e.g. ['vt', 'passivetotal'])")],
    verify_ssl: Annotated[bool, Field(True)] = True,
    force: Annotated[bool, Field(False, description="Force enrichment even if already enriched")] = False,
) -> dict:
    headers = {
        "Authorization": secrets.get("MISP_API_KEY"),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    url = f"{base_url.rstrip('/')}/attributes/enrich/{attribute_id}"
    params = {module: "true" for module in modules}
    if force:
        params["force"] = "true"

    async with httpx.AsyncClient(verify=verify_ssl) as client:
        response = await client.post(url, headers=headers, params=params)
        response.raise_for_status()
        return response.json()
