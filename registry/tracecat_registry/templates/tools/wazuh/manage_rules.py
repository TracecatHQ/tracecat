from typing import Annotated, Optional
import httpx
from pydantic import Field
from tracecat_registry import RegistrySecret, registry, secrets

wazuh_secret = RegistrySecret(name="wazuh_wui", keys=["WAZUH_WUI_USERNAME", "WAZUH_WUI_PASSWORD"])

@registry.register(
    default_title="Manage Wazuh Rules",
    description="Liste, récupère, ajoute, modifie ou supprime des règles Wazuh via l’API REST officielle.",
    display_group="Wazuh",
    doc_url="https://documentation.wazuh.com/current/user-manual/api/reference.html#tag/File",
    namespace="tools.wazuh",
    secrets=[wazuh_secret],
)
async def manage_rules(
    base_url: Annotated[str, Field(..., description="Base URL de l’API Wazuh")],
    operation: Annotated[str, Field(..., description="Opération à effectuer : list, get, add, update, delete")],
    rule_ids: Annotated[Optional[str], Field(None, description="ID(s) de la règle à consulter ou supprimer, séparés par des virgules")],
    filename: Annotated[Optional[str], Field(None, description="Nom du fichier XML (ex: custom_rules.xml) pour add/update")],
    rule_content: Annotated[Optional[str], Field(None, description="Contenu XML à envoyer dans le fichier pour add/update")],
    overwrite: Annotated[bool, Field(True, description="Écraser le fichier s’il existe (pour add/update)")],
    verify_ssl: Annotated[bool, Field(True, description="Vérifie les certificats SSL lors des appels API")],
    auth_token_exp_timeout: Annotated[int, Field(900, description="Durée de validité du token JWT (en secondes)")],
) -> dict:
    """
    Gère les règles Wazuh via l'API :
    - list    : GET /rules
    - get     : GET /rules/{rule_ids}
    - delete  : DELETE /rules/{rule_ids}
    - add     : PUT /manager/files/{filename}?relative_dirname=rules
    - update  : idem que add, mais permet de modifier un fichier existant (avec overwrite=True)
    """
    async with httpx.AsyncClient(verify=verify_ssl) as client:
        # Authentification
        resp = await client.post(
            f"{base_url}/security/user/authenticate",
            headers={"Content-Type": "application/json"},
            json={"auth_token_exp_timeout": auth_token_exp_timeout},
            auth=(secrets.get("WAZUH_WUI_USERNAME"), secrets.get("WAZUH_WUI_PASSWORD")),
        )
        resp.raise_for_status()
        token = resp.json()["data"]["token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Opérations
        if operation == "list":
            r = await client.get(f"{base_url}/rules", headers=headers)

        elif operation == "get":
            if not rule_ids:
                raise ValueError("Paramètre 'rule_ids' requis pour l’opération 'get'")
            r = await client.get(f"{base_url}/rules/{rule_ids}", headers=headers)

        elif operation == "delete":
            if not rule_ids:
                raise ValueError("Paramètre 'rule_ids' requis pour l’opération 'delete'")
            r = await client.delete(f"{base_url}/rules/{rule_ids}", headers=headers)

        elif operation in ["add", "update"]:
            if not filename or not rule_content:
                raise ValueError("Les paramètres 'filename' et 'rule_content' sont requis pour 'add' et 'update'")
            r = await client.put(
                f"{base_url}/manager/files/{filename}",
                headers={**headers, "Content-Type": "application/xml"},
                params={
                    "relative_dirname": "rules",
                    "overwrite": str(overwrite).lower()
                },
                content=rule_content.encode("utf-8")
            )

        else:
            raise ValueError("Opération inconnue : choisir parmi 'list', 'get', 'add', 'update', 'delete'")

        r.raise_for_status()
        return r.json()
