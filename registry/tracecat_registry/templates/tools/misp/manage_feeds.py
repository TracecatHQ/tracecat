from typing import Annotated, Optional, list
import httpx
from pydantic import Field
from tracecat_registry import RegistrySecret, registry, secrets

misp_secret = RegistrySecret(name="misp_api", keys=["MISP_API_KEY"])

@registry.register(
    default_title="List MISP Feeds",
    description="Get a list of all configured feeds in MISP.",
    display_group="MISP",
    doc_url="https://www.misp-project.org/openapi/#tag/Feeds/operation/getFeeds",
    namespace="tools.misp",
    secrets=[misp_secret],
)
async def list_misp_feeds(
    base_url: Annotated[str, Field(..., description="Base URL of the MISP instance")],
    verify_ssl: Annotated[bool, Field(True, description="If False, disables SSL verification.")] = True,
) -> dict:
    headers = {
        "Authorization": secrets.get("MISP_API_KEY"),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    url = f"{base_url.rstrip('/')}/feeds/index"
    
    async with httpx.AsyncClient(verify=verify_ssl) as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        return response.json()

@registry.register(
    default_title="Add MISP Feed",
    description="Add a new feed to MISP.",
    display_group="MISP",
    doc_url="https://www.misp-project.org/openapi/#tag/Feeds/operation/addFeed",
    namespace="tools.misp",
    secrets=[misp_secret],
)
async def add_misp_feed(
    base_url: Annotated[str, Field(..., description="Base URL of the MISP instance")],
    name: Annotated[str, Field(..., description="Name of the feed")],
    url: Annotated[str, Field(..., description="URL of the feed")],
    provider: Annotated[str, Field(..., description="Provider of the feed")],
    verify_ssl: Annotated[bool, Field(True, description="If False, disables SSL verification.")] = True,
    enabled: Annotated[bool, Field(True, description="Whether the feed should be enabled")] = True,
    source_format: Annotated[str, Field("misp", description="Format of the feed (misp, csv, etc.)")] = "misp",
    fixed_event: Annotated[bool, Field(False, description="Whether to use fixed event IDs")] = False,
    delta_merge: Annotated[bool, Field(False, description="Whether to use delta merging")] = False,
    headers: Annotated[Optional[dict], Field(None, description="Additional headers for the feed")] = None,
) -> dict:
    headers = {
        "Authorization": secrets.get("MISP_API_KEY"),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    url = f"{base_url.rstrip('/')}/feeds/add"
    data = {
        "Feed": {
            "name": name,
            "url": url,
            "provider": provider,
            "enabled": enabled,
            "source_format": source_format,
            "fixed_event": fixed_event,
            "delta_merge": delta_merge,
        }
    }

    if headers:
        data["Feed"]["headers"] = headers

    async with httpx.AsyncClient(verify=verify_ssl) as client:
        response = await client.post(url, headers=headers, json=data)
        response.raise_for_status()
        return response.json()

@registry.register(
    default_title="Fetch Feed",
    description="Manually fetch a feed in MISP.",
    display_group="MISP",
    doc_url="https://www.misp-project.org/openapi/#tag/Feeds/operation/fetchFeed",
    namespace="tools.misp",
    secrets=[misp_secret],
)
async def fetch_misp_feed(
    base_url: Annotated[str, Field(..., description="Base URL of the MISP instance")],
    feed_id: Annotated[int, Field(..., description="ID of the feed to fetch")],
    verify_ssl: Annotated[bool, Field(True, description="If False, disables SSL verification.")] = True,
) -> dict:
    headers = {
        "Authorization": secrets.get("MISP_API_KEY"),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    url = f"{base_url.rstrip('/')}/feeds/fetchFromFeed/{feed_id}"
    
    async with httpx.AsyncClient(verify=verify_ssl) as client:
        response = await client.post(url, headers=headers)
        response.raise_for_status()
        return response.json() 