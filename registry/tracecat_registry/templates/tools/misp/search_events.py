from typing import Annotated, Optional, list
import httpx
from pydantic import Field
from tracecat_registry import RegistrySecret, registry, secrets
from datetime import datetime

misp_secret = RegistrySecret(name="misp_api", keys=["MISP_API_KEY"])

@registry.register(
    default_title="Search MISP Events",
    description="Search for MISP events with various filters and parameters.",
    display_group="MISP",
    doc_url="https://www.misp-project.org/openapi/#tag/Events/operation/searchEvents",
    namespace="tools.misp",
    secrets=[misp_secret],
)
async def search_misp_events(
    base_url: Annotated[str, Field(..., description="Base URL of the MISP instance")],
    verify_ssl: Annotated[bool, Field(True, description="If False, disables SSL verification.")] = True,
    event_id: Annotated[Optional[int], Field(None, description="Search for a specific event ID")] = None,
    event_uuid: Annotated[Optional[str], Field(None, description="Search for a specific event UUID")] = None,
    event_tags: Annotated[Optional[list[str]], Field(None, description="List of tags to search for")] = None,
    from_date: Annotated[Optional[str], Field(None, description="Search events from this date (YYYY-MM-DD)")] = None,
    to_date: Annotated[Optional[str], Field(None, description="Search events until this date (YYYY-MM-DD)")] = None,
    threat_level_id: Annotated[Optional[int], Field(None, description="Filter by threat level (1-4)")] = None,
    analysis: Annotated[Optional[int], Field(None, description="Filter by analysis level (0-2)")] = None,
    published: Annotated[Optional[bool], Field(None, description="Filter by publication status")] = None,
    return_format: Annotated[str, Field("json", description="Return format (json, xml, csv, etc.)")] = "json",
) -> dict:
    headers = {
        "Authorization": secrets.get("MISP_API_KEY"),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    url = f"{base_url.rstrip('/')}/events/restSearch"
    params = {
        "returnFormat": return_format,
    }

    if event_id is not None:
        params["eventid"] = event_id
    if event_uuid:
        params["uuid"] = event_uuid
    if event_tags:
        params["tags"] = event_tags
    if from_date:
        params["from"] = from_date
    if to_date:
        params["to"] = to_date
    if threat_level_id is not None:
        params["threat_level_id"] = threat_level_id
    if analysis is not None:
        params["analysis"] = analysis
    if published is not None:
        params["published"] = str(published).lower()

    async with httpx.AsyncClient(verify=verify_ssl) as client:
        response = await client.post(url, headers=headers, params=params)
        response.raise_for_status()
        return response.json() 