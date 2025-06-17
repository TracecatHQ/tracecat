from typing import Annotated, Optional, list
import httpx
from pydantic import Field
from tracecat_registry import RegistrySecret, registry, secrets

misp_secret = RegistrySecret(name="misp_api", keys=["MISP_API_KEY"])

@registry.register(
    default_title="List MISP Servers",
    description="Get a list of all configured MISP servers for synchronization.",
    display_group="MISP",
    doc_url="https://www.misp-project.org/openapi/#tag/Servers/operation/getServers",
    namespace="tools.misp",
    secrets=[misp_secret],
)
async def list_misp_servers(
    base_url: Annotated[str, Field(..., description="Base URL of the MISP instance")],
    verify_ssl: Annotated[bool, Field(True, description="If False, disables SSL verification.")] = True,
) -> dict:
    headers = {
        "Authorization": secrets.get("MISP_API_KEY"),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    url = f"{base_url.rstrip('/')}/servers/index"
    
    async with httpx.AsyncClient(verify=verify_ssl) as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        return response.json()

@registry.register(
    default_title="Add MISP Server",
    description="Add a new MISP server for synchronization.",
    display_group="MISP",
    doc_url="https://www.misp-project.org/openapi/#tag/Servers/operation/addServer",
    namespace="tools.misp",
    secrets=[misp_secret],
)
async def add_misp_server(
    base_url: Annotated[str, Field(..., description="Base URL of the MISP instance")],
    server_url: Annotated[str, Field(..., description="URL of the remote MISP server")],
    auth_key: Annotated[str, Field(..., description="Authentication key for the remote server")],
    name: Annotated[Optional[str], Field(None, description="Name of the server")] = None,
    push: Annotated[bool, Field(False, description="Enable push synchronization")] = False,
    pull: Annotated[bool, Field(False, description="Enable pull synchronization")] = False,
    verify_ssl: Annotated[bool, Field(True, description="If False, disables SSL verification.")] = True,
) -> dict:
    headers = {
        "Authorization": secrets.get("MISP_API_KEY"),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    url = f"{base_url.rstrip('/')}/servers/add"
    data = {
        "Server": {
            "url": server_url,
            "authkey": auth_key,
            "push": push,
            "pull": pull,
        }
    }

    if name:
        data["Server"]["name"] = name

    async with httpx.AsyncClient(verify=verify_ssl) as client:
        response = await client.post(url, headers=headers, json=data)
        response.raise_for_status()
        return response.json()

@registry.register(
    default_title="Test MISP Server Connection",
    description="Test the connection to a MISP server.",
    display_group="MISP",
    doc_url="https://www.misp-project.org/openapi/#tag/Servers/operation/testConnection",
    namespace="tools.misp",
    secrets=[misp_secret],
)
async def test_misp_server_connection(
    base_url: Annotated[str, Field(..., description="Base URL of the MISP instance")],
    server_id: Annotated[int, Field(..., description="ID of the server to test")],
    verify_ssl: Annotated[bool, Field(True, description="If False, disables SSL verification.")] = True,
) -> dict:
    headers = {
        "Authorization": secrets.get("MISP_API_KEY"),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    url = f"{base_url.rstrip('/')}/servers/testConnection/{server_id}"
    
    async with httpx.AsyncClient(verify=verify_ssl) as client:
        response = await client.post(url, headers=headers)
        response.raise_for_status()
        return response.json() 