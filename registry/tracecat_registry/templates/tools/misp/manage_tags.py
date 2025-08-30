from typing import Annotated, Optional, list
import httpx
from pydantic import Field
from tracecat_registry import RegistrySecret, registry, secrets

misp_secret = RegistrySecret(name="misp_api", keys=["MISP_API_KEY"])

@registry.register(
    default_title="List MISP Tags",
    description="Get a list of all available tags in MISP.",
    display_group="MISP",
    doc_url="https://www.misp-project.org/openapi/#tag/Tags/operation/getTags",
    namespace="tools.misp",
    secrets=[misp_secret],
)
async def list_misp_tags(
    base_url: Annotated[str, Field(..., description="Base URL of the MISP instance")],
    verify_ssl: Annotated[bool, Field(True, description="If False, disables SSL verification.")] = True,
) -> dict:
    headers = {
        "Authorization": secrets.get("MISP_API_KEY"),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    url = f"{base_url.rstrip('/')}/tags/index"
    
    async with httpx.AsyncClient(verify=verify_ssl) as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        return response.json()

@registry.register(
    default_title="Add Tag to MISP Event",
    description="Add one or more tags to a MISP event.",
    display_group="MISP",
    doc_url="https://www.misp-project.org/openapi/#tag/Events/operation/addTag",
    namespace="tools.misp",
    secrets=[misp_secret],
)
async def add_tags_to_misp_event(
    base_url: Annotated[str, Field(..., description="Base URL of the MISP instance")],
    event_id: Annotated[int, Field(..., description="ID of the MISP event")],
    tags: Annotated[list[str], Field(..., description="List of tag names to add")],
    verify_ssl: Annotated[bool, Field(True, description="If False, disables SSL verification.")] = True,
) -> dict:
    headers = {
        "Authorization": secrets.get("MISP_API_KEY"),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    base_url = base_url.rstrip("/")
    results = []

    async with httpx.AsyncClient(verify=verify_ssl) as client:
        for tag in tags:
            response = await client.post(
                f"{base_url}/events/addTag/{event_id}",
                headers=headers,
                json={"Tag": {"name": tag}}
            )
            response.raise_for_status()
            results.append(response.json())

    return {"results": results}

@registry.register(
    default_title="Remove Tag from MISP Event",
    description="Remove one or more tags from a MISP event.",
    display_group="MISP",
    doc_url="https://www.misp-project.org/openapi/#tag/Events/operation/removeTag",
    namespace="tools.misp",
    secrets=[misp_secret],
)
async def remove_tags_from_misp_event(
    base_url: Annotated[str, Field(..., description="Base URL of the MISP instance")],
    event_id: Annotated[int, Field(..., description="ID of the MISP event")],
    tags: Annotated[list[str], Field(..., description="List of tag names to remove")],
    verify_ssl: Annotated[bool, Field(True, description="If False, disables SSL verification.")] = True,
) -> dict:
    headers = {
        "Authorization": secrets.get("MISP_API_KEY"),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    base_url = base_url.rstrip("/")
    results = []

    async with httpx.AsyncClient(verify=verify_ssl) as client:
        for tag in tags:
            response = await client.post(
                f"{base_url}/events/removeTag/{event_id}",
                headers=headers,
                json={"Tag": {"name": tag}}
            )
            response.raise_for_status()
            results.append(response.json())

    return {"results": results} 