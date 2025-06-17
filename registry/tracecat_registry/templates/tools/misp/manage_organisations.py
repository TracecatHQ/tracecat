from typing import Annotated, Optional, list
import httpx
from pydantic import Field
from tracecat_registry import RegistrySecret, registry, secrets

misp_secret = RegistrySecret(name="misp_api", keys=["MISP_API_KEY"])

@registry.register(
    default_title="List MISP Organisations",
    description="Get a list of all organizations in MISP.",
    display_group="MISP",
    doc_url="https://www.misp-project.org/openapi/#tag/Organisations/operation/getOrganisations",
    namespace="tools.misp",
    secrets=[misp_secret],
)
async def list_misp_organisations(
    base_url: Annotated[str, Field(..., description="Base URL of the MISP instance")],
    verify_ssl: Annotated[bool, Field(True, description="If False, disables SSL verification.")] = True,
) -> dict:
    headers = {
        "Authorization": secrets.get("MISP_API_KEY"),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    url = f"{base_url.rstrip('/')}/organisations/index"
    
    async with httpx.AsyncClient(verify=verify_ssl) as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        return response.json()

@registry.register(
    default_title="Get MISP Organisation",
    description="Get details about a specific organization in MISP.",
    display_group="MISP",
    doc_url="https://www.misp-project.org/openapi/#tag/Organisations/operation/getOrganisationById",
    namespace="tools.misp",
    secrets=[misp_secret],
)
async def get_misp_organisation(
    base_url: Annotated[str, Field(..., description="Base URL of the MISP instance")],
    org_id: Annotated[int, Field(..., description="ID of the organization to get")],
    verify_ssl: Annotated[bool, Field(True, description="If False, disables SSL verification.")] = True,
) -> dict:
    headers = {
        "Authorization": secrets.get("MISP_API_KEY"),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    url = f"{base_url.rstrip('/')}/organisations/view/{org_id}"
    
    async with httpx.AsyncClient(verify=verify_ssl) as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        return response.json()

@registry.register(
    default_title="Create MISP Organisation",
    description="Create a new organization in MISP.",
    display_group="MISP",
    doc_url="https://www.misp-project.org/openapi/#tag/Organisations/operation/addOrganisation",
    namespace="tools.misp",
    secrets=[misp_secret],
)
async def create_misp_organisation(
    base_url: Annotated[str, Field(..., description="Base URL of the MISP instance")],
    name: Annotated[str, Field(..., description="Name of the organization")],
    uuid: Annotated[Optional[str], Field(None, description="UUID of the organization")] = None,
    description: Annotated[Optional[str], Field(None, description="Description of the organization")] = None,
    type: Annotated[Optional[str], Field(None, description="Type of the organization")] = None,
    nationality: Annotated[Optional[str], Field(None, description="Nationality of the organization")] = None,
    sector: Annotated[Optional[str], Field(None, description="Sector of the organization")] = None,
    verify_ssl: Annotated[bool, Field(True, description="If False, disables SSL verification.")] = True,
) -> dict:
    headers = {
        "Authorization": secrets.get("MISP_API_KEY"),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    url = f"{base_url.rstrip('/')}/organisations/add"
    data = {
        "Organisation": {
            "name": name,
        }
    }

    if uuid:
        data["Organisation"]["uuid"] = uuid
    if description:
        data["Organisation"]["description"] = description
    if type:
        data["Organisation"]["type"] = type
    if nationality:
        data["Organisation"]["nationality"] = nationality
    if sector:
        data["Organisation"]["sector"] = sector

    async with httpx.AsyncClient(verify=verify_ssl) as client:
        response = await client.post(url, headers=headers, json=data)
        response.raise_for_status()
        return response.json() 