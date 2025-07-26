from typing import Annotated, Optional
import httpx
from pydantic import Field
from tracecat_registry import RegistrySecret, registry, secrets

wazuh_secret = RegistrySecret(name="wazuh_wui", keys=["WAZUH_WUI_USERNAME", "WAZUH_WUI_PASSWORD"])

@registry.register(
    default_title="Manage Wazuh Alerts",
    description="Gère les opérations sur les alertes Wazuh (list, get, delete).",
    display_group="Wazuh",
    doc_url="https://documentation.wazuh.com/current/user-manual/api/reference.html#tag/Alerts",
    namespace="tools.wazuh",
    secrets=[wazuh_secret],
)
async def manage_alerts(
    base_url: Annotated[str, Field(..., description="Base URL de l’API Wazuh")],
    operation: Annotated[str, Field(..., description="Opération à effectuer (list, get, delete)")],
    alert_id: Annotated[Optional[str], Field(None, description="ID de l'alerte (requis pour get, delete)")],
    start_date: Annotated[Optional[str], Field(None, description="Date de début pour filtrer les alertes (YYYY-MM-DD HH:mm:ss)")],
    end_date: Annotated[Optional[str], Field(None, description="Date de fin pour filtrer les alertes (YYYY-MM-DD HH:mm:ss)")],
    level: Annotated[Optional[int], Field(None, description="Niveau minimum d'alerte à récupérer")],
    rule_id: Annotated[Optional[str], Field(None, description="Filtrer les alertes par ID de règle")],
    verify_ssl: Annotated[bool, Field(True, description="Vérifier les certificats SSL")],
    auth_token_exp_timeout: Annotated[int, Field(900, description="Durée de validité du token d'authentification (en secondes)")],
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

        if operation == "list":
            params = {}
            if start_date:
                params["start_date"] = start_date
            if end_date:
                params["end_date"] = end_date
            if level is not None:
                params["level"] = level
            if rule_id:
                params["rule_id"] = rule_id
            r = await client.get(f"{base_url}/alerts", headers=headers, params=params)
        elif operation == "get":
            if not alert_id:
                raise ValueError("alert_id est requis pour l'opération 'get'")
            r = await client.get(f"{base_url}/alerts/{alert_id}", headers=headers)
        elif operation == "delete":
            if not alert_id:
                raise ValueError("alert_id est requis pour l'opération 'delete'")
            r = await client.delete(f"{base_url}/alerts/{alert_id}", headers=headers)
        else:
            raise ValueError("Opération inconnue : choisir parmi 'list', 'get', 'delete'")
        r.raise_for_status()
        return r.json() 
