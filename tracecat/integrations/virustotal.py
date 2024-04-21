"""Integrations with VirusTotal REST API.

This is not a 1-to-1 mapping to the VirusTotal API.
We might use multiple API endpoints in a single Action for user convenience.
For example,

Required credentials: `virustotal` secret with `VT_API_KEY` key.

API reference: https://docs.virustotal.com/reference/overview
"""

import base64
import os
from typing import Any

import httpx

from tracecat.integrations._registry import registry
from tracecat.logger import standard_logger

logger = standard_logger(__name__)


VT_BASE_URL = "https://www.virustotal.com/api/v3/"


def create_virustotal_client() -> httpx.Client:
    return httpx.Client(base_url=VT_BASE_URL)


@registry.register(description="Get file report by hash", secrets=["virustotal"])
def get_file_report(file_hash: str) -> dict[str, Any]:
    """Returns File object: https://docs.virustotal.com/reference/files"""
    with create_virustotal_client() as client:
        rsp = client.get(
            f"urls/{file_hash}", headers={"x-apikey": os.environ["VT_API_KEY"]}
        )
        rsp.raise_for_status()
        return rsp.json()


@registry.register(description="Get URL analysis report by URL", secrets=["virustotal"])
def get_url_report(url: str) -> dict[str, Any]:
    """Returns URL object: https://docs.virustotal.com/reference/url-object"""
    # Recipe from https://docs.virustotal.com/reference/url#url-identifiers
    url_id = base64.urlsafe_b64encode(url.encode()).decode().strip("=")
    with create_virustotal_client() as client:
        rsp = client.get(
            f"urls/{url_id}",
            headers={
                "x-apikey": os.environ["VT_API_KEY"],
            },
        )
        rsp.raise_for_status()
        return rsp.json()


@registry.register(description="Get domain report", secrets=["virustotal"])
def get_domain_report(domain: str) -> dict[str, Any]:
    """Returns Domain object: https://docs.virustotal.com/reference/domains-object"""
    with create_virustotal_client() as client:
        rsp = client.get(
            f"domains/{domain}",
            headers={
                "x-apikey": os.environ["VT_API_KEY"],
            },
        )
        rsp.raise_for_status()
        return rsp.json()


@registry.register(description="Get IP address report", secrets=["virustotal"])
def get_ip_address_report(ip: str) -> dict[str, Any]:
    """Returns IP object: https://docs.virustotal.com/reference/ip-object"""
    with create_virustotal_client() as client:
        rsp = client.get(
            f"ip_addresses/{ip}",
            headers={
                "x-apikey": os.environ["VT_API_KEY"],
            },
        )
        rsp.raise_for_status()
        return rsp.json()
