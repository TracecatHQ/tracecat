from typing import Annotated, Optional
import httpx
from pydantic import Field
from tracecat_registry import RegistrySecret, registry, secrets

wazuh_secret = RegistrySecret(name="wazuh_wui", keys=["WAZUH_WUI_USERNAME", "WAZUH_WUI_PASSWORD"])

@registry.register(
    default_title="Manage Wazuh Agents",
    description="Gère les opérations sur les agents Wazuh (list, get, add, remove, restart).",
    display_group="Wazuh",
    namespace="tools.wazuh",
    secrets=[wazuh_secret],
)
async def manage_agents(
    base_url: Annotated[str, Field(..., description="Base URL de l’API Wazuh")],
    operation: Annotated[str, Field(..., description="Opération à effectuer")],
    agent_id: Annotated[Optional[str], Field(None)],
    agent_name: Annotated[Optional[str], Field(None)],
    agent_ip: Annotated[Optional[str], Field(None)],
    verify_ssl: Annotated[bool, Field(True)],
    auth_token_exp_timeout: Annotated[int, Field(900)],
) -> dict:
    # Authentification
    async with httpx.AsyncClient(verify=verify_ssl) as client:
        resp = await client.post(
            f"{base_url}/security/user/authenticate",
            headers={"Content-Type": "application/json"},
            json={"auth_token_exp_timeout": auth_token_exp_timeout},
            auth=(secrets.get("WAZUH_WUI_USERNAME"), secrets.get("WAZUH_WUI_PASSWORD")),
        )
        resp.raise_for_status()
        token = resp.json()["data"]["token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Dispatch selon l’opération
        if operation == "list":
            r = await client.get(f"{base_url}/agents", headers=headers)
        elif operation == "get":
            r = await client.get(f"{base_url}/agents/{agent_id}", headers=headers)
        elif operation == "add":
            r = await client.post(f"{base_url}/agents", headers={**headers, "Content-Type": "application/json"}, json={"name": agent_name, "ip": agent_ip})
        elif operation == "remove":
            r = await client.delete(f"{base_url}/agents/{agent_id}", headers=headers)
        elif operation == "restart":
            r = await client.put(f"{base_url}/agents/{agent_id}/restart", headers=headers)
        else:
            raise ValueError("Opération inconnue")
        r.raise_for_status()
        return r.json()
