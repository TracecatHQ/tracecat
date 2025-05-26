from typing import Annotated, List, Optional
import httpx
from pydantic import Field
from tracecat_registry import RegistrySecret, registry, secrets
from datetime import datetime

misp_secret = RegistrySecret(name="misp_api", keys=["MISP_API_KEY"])

IOC_CATEGORY_MAPPING = {
    "ip-src": "Network activity",
    "ip-dst": "Network activity",
    "domain": "Network activity",
    "hostname": "Network activity",
    "url": "Network activity",
    "sha256": "Payload delivery",
    "sha1": "Payload delivery",
    "md5": "Payload delivery",
    "filename": "Artifacts dropped",
    "email-src": "Payload delivery",
    "email-dst": "Payload delivery",
    "mutex": "Persistence mechanism",
    "regkey": "Persistence mechanism",
}

def get_category_for_ioc_type(ioc_type: str) -> str:
    return IOC_CATEGORY_MAPPING.get(ioc_type.lower(), "Network activity")

@registry.register(
    default_title="Create MISP Event from IOC",
    description="Ingests a generic alert into MISP by creating an event and adding an IOC as attribute.",
    display_group="MISP",
    namespace="tools.misp",
    secrets=[misp_secret],
)
async def create_misp_event_from_ioc(
    base_url: Annotated[str, Field(..., description="Base URL of the MISP instance")],
    ioc_value: Annotated[str, Field(..., description="The IOC value to register in MISP")],
    ioc_type: Annotated[str, Field(..., description="MISP-compatible IOC type")],
    event_info: Annotated[str, Field(..., description="Short description of the alert")],
    threat_level_id: Annotated[int, Field(3)],
    distribution: Annotated[int, Field(0)],
    to_ids: Annotated[bool, Field(True)],
    verify_ssl: Annotated[bool, Field(True)],
    tags: Annotated[Optional[List[str]], Field(None)] = None,
) -> dict:
    headers = {
        "Authorization": secrets.get("MISP_API_KEY"),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    category = get_category_for_ioc_type(ioc_type)

    event_payload = {
        "Event": {
            "info": event_info,
            "analysis": "2",
            "threat_level_id": threat_level_id,
            "distribution": distribution,
            "date": datetime.utcnow().strftime("%Y-%m-%d"),
            "Attribute": [{
                "type": ioc_type,
                "category": category,
                "value": ioc_value,
                "to_ids": to_ids
            }],
            **({"Tag": [{"name": tag} for tag in tags]} if tags else {})
        }
    }

    async with httpx.AsyncClient(verify=verify_ssl) as client:
        response = await client.post(
            f"{base_url.rstrip('/')}/events",
            headers=headers,
            json=event_payload,
        )
        response.raise_for_status()
        return response.json()
