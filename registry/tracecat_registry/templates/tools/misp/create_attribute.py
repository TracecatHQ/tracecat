from typing import Annotated, Optional, List
import httpx
from pydantic import Field
from tracecat_registry import RegistrySecret, registry, secrets

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

VALID_CATEGORIES = set([
    "Internal reference", "Targeting data", "Antivirus detection", "Payload delivery",
    "Artifacts dropped", "Payload installation", "Persistence mechanism", "Network activity",
    "Payload type", "Attribution", "External analysis", "Financial fraud", "Support Tool",
    "Social network", "Person", "Other"
])

def get_category_for_ioc_type(ioc_type: str) -> str:
    return IOC_CATEGORY_MAPPING.get(ioc_type.lower(), "Network activity")

@registry.register(
    default_title="Add Attribute to MISP Event",
    description="Adds a custom attribute to an existing MISP event. Supports manual or inferred category.",
    display_group="MISP",
    namespace="tools.misp",
    secrets=[misp_secret],
)
async def add_attribute_to_misp_event(
    base_url: Annotated[str, Field(..., description="Base URL of the MISP instance")],
    event_id: Annotated[int, Field(..., description="ID of the MISP event to add the attribute to")],
    ioc_value: Annotated[str, Field(..., description="The IOC value to add")],
    ioc_type: Annotated[str, Field(..., description="MISP-compatible IOC type")],
    to_ids: Annotated[bool, Field(True)],
    verify_ssl: Annotated[bool, Field(True)] = True,
    category: Annotated[Optional[str], Field(None)] = None,
    comment: Annotated[Optional[str], Field(None)] = None,
    event_info: Annotated[Optional[str], Field(None)] = None,
    tags: Annotated[Optional[List[str]], Field(None)] = None,
    threat_level_id: Annotated[Optional[int], Field(None)] = None,
) -> dict:
    headers = {
        "Authorization": secrets.get("MISP_API_KEY"),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    selected_category = category or get_category_for_ioc_type(ioc_type)
    if category and category not in VALID_CATEGORIES:
        raise ValueError(f"Invalid category '{category}'. Must be one of: {', '.join(sorted(VALID_CATEGORIES))}")

    attribute_payload = {
        "Attribute": {
            "type": ioc_type,
            "value": ioc_value,
            "category": selected_category,
            "to_ids": to_ids,
            **({"comment": comment.strip()} if comment and comment.strip() else {})
        }
    }

    base_url = base_url.rstrip("/")

    async with httpx.AsyncClient(verify=verify_ssl) as client:
        attr_resp = await client.post(
            f"{base_url}/attributes/add/{event_id}",
            headers=headers,
            json=attribute_payload
        )
        attr_resp.raise_for_status()
        result = attr_resp.json()

        if tags:
            for tag in tags:
                await client.post(
                    f"{base_url}/events/addTag/{event_id}",
                    headers=headers,
                    json={"Tag": {"name": tag}}
                )

        if event_info or threat_level_id is not None:
            update_payload = {
                "Event": {
                    **({"info": event_info} if event_info else {}),
                    **({"threat_level_id": threat_level_id} if threat_level_id is not None else {})
                }
            }
            await client.post(
                f"{base_url}/events/edit/{event_id}",
                headers=headers,
                json=update_payload
            )

        return result
