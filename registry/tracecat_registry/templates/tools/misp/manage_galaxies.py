from typing import Annotated, Optional, list
import httpx
from pydantic import Field
from tracecat_registry import RegistrySecret, registry, secrets

misp_secret = RegistrySecret(name="misp_api", keys=["MISP_API_KEY"])

@registry.register(
    default_title="List MISP Galaxies",
    description="Get a list of all available galaxies in MISP.",
    display_group="MISP",
    doc_url="https://www.misp-project.org/openapi/#tag/Galaxies/operation/getGalaxies",
    namespace="tools.misp",
    secrets=[misp_secret],
)
async def list_misp_galaxies(
    base_url: Annotated[str, Field(..., description="Base URL of the MISP instance")],
    verify_ssl: Annotated[bool, Field(True, description="If False, disables SSL verification.")] = True,
) -> dict:
    headers = {
        "Authorization": secrets.get("MISP_API_KEY"),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    url = f"{base_url.rstrip('/')}/galaxies/index"
    
    async with httpx.AsyncClient(verify=verify_ssl) as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        return response.json()

@registry.register(
    default_title="Get Galaxy Clusters",
    description="Get clusters for a specific galaxy in MISP.",
    display_group="MISP",
    doc_url="https://www.misp-project.org/openapi/#tag/Galaxies/operation/getGalaxyClusters",
    namespace="tools.misp",
    secrets=[misp_secret],
)
async def get_galaxy_clusters(
    base_url: Annotated[str, Field(..., description="Base URL of the MISP instance")],
    galaxy_id: Annotated[int, Field(..., description="ID of the galaxy to get clusters from")],
    verify_ssl: Annotated[bool, Field(True, description="If False, disables SSL verification.")] = True,
) -> dict:
    headers = {
        "Authorization": secrets.get("MISP_API_KEY"),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    url = f"{base_url.rstrip('/')}/galaxies/view/{galaxy_id}"
    
    async with httpx.AsyncClient(verify=verify_ssl) as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        return response.json()

@registry.register(
    default_title="Add Galaxy to Event",
    description="Add a galaxy cluster to a MISP event.",
    display_group="MISP",
    doc_url="https://www.misp-project.org/openapi/#tag/Galaxies/operation/addGalaxyCluster",
    namespace="tools.misp",
    secrets=[misp_secret],
)
async def add_galaxy_to_event(
    base_url: Annotated[str, Field(..., description="Base URL of the MISP instance")],
    event_id: Annotated[int, Field(..., description="ID of the event to add the galaxy to")],
    galaxy_id: Annotated[int, Field(..., description="ID of the galaxy to add")],
    cluster_id: Annotated[int, Field(..., description="ID of the cluster to add")],
    verify_ssl: Annotated[bool, Field(True, description="If False, disables SSL verification.")] = True,
) -> dict:
    headers = {
        "Authorization": secrets.get("MISP_API_KEY"),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    url = f"{base_url.rstrip('/')}/galaxies/attachClusterToEvent/{event_id}"
    data = {
        "GalaxyCluster": {
            "galaxy_id": galaxy_id,
            "cluster_id": cluster_id
        }
    }

    async with httpx.AsyncClient(verify=verify_ssl) as client:
        response = await client.post(url, headers=headers, json=data)
        response.raise_for_status()
        return response.json() 