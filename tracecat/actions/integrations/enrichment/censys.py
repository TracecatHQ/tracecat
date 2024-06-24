"""Requires secret named `censys` with key `CENSYS_API_KEY`."""

import ipaddress
import os
from typing import Annotated, Any

import httpx

from tracecat.registry import Field, registry

CENSYS_BASE_URL = "https://search.censys.io/api/v2"


def create_censys_client() -> httpx.AsyncClient:
    CENSYS_API_KEY = os.getenv("CENSYS_API_KEY")
    if CENSYS_API_KEY is None:
        raise ValueError("CENSYS_API_KEY is not set")
    client = httpx.AsyncClient(
        base_url=CENSYS_BASE_URL,
        headers={"Authorization": f"Bearer {CENSYS_API_KEY}"},
    )
    return client


@registry.register(
    default_title="Analyze IP address",
    description="Analyze an IP address using Censys.",
    display_group="Censys",
    namespace="censys",
    secrets=["censys"],
)
async def analyze_ip_address(
    ip_address: Annotated[str, Field(..., description="The IP address to analyze")],
) -> dict[str, Any]:
    try:
        ipaddress.ip_address(ip_address)
    except ValueError as err:
        raise ValueError("Invalid IP address format") from err

    async with create_censys_client() as client:
        response = await client.get(f"/hosts/{ip_address}")
        response.raise_for_status()
        return response.json()
