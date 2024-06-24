"""Requires secret named 'shodan' with key 'SHODAN_API_KEY'."""

import os
from typing import Annotated, Any

import shodan

from tracecat.registry import Field, registry


def create_shodan_client():
    SHODAN_API_KEY = os.getenv("SHODAN_API_KEY")
    if SHODAN_API_KEY is None:
        raise ValueError("SHODAN_API_KEY is not set")
    api = shodan.Shodan(SHODAN_API_KEY)
    return api


@registry.register(
    default_title="Analyze URL",
    description="Analyze a URL using Shodan.",
    display_group="Shodan",
    namespace="integrations.shodan",
    secrets=["shodan"],
)
async def analyze_url(
    url: Annotated[str, Field(..., description="The URL to analyze")],
) -> dict[str, Any]:
    try:
        api = create_shodan_client()
        # Extract the hostname from the URL
        hostname = url.split("//")[-1].split("/")[0]
        # Perform the search using the hostname filter
        result = api.search(f"hostname:{hostname}")
        return result
    except shodan.APIError as e:
        return {"error": str(e)}


@registry.register(
    default_title="Analyze IP address",
    description="Analyze an IP address using Shodan.",
    display_group="Shodan",
    namespace="integrations.shodan",
    secrets=["shodan"],
)
async def analyze_ip_address(
    ip_address: Annotated[str, Field(..., description="The IP address to analyze")],
) -> dict[str, Any]:
    try:
        api = create_shodan_client()
        # Perform the search using the IP address
        result = api.host(ip_address)
        return result
    except shodan.APIError as e:
        return {"error": str(e)}
