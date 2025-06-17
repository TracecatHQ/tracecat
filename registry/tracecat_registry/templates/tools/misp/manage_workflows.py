from typing import Annotated, Optional, list
import httpx
from pydantic import Field
from tracecat_registry import RegistrySecret, registry, secrets

misp_secret = RegistrySecret(name="misp_api", keys=["MISP_API_KEY"])

@registry.register(
    default_title="List MISP Workflows",
    description="Get a list of all available workflows in MISP.",
    display_group="MISP",
    doc_url="https://www.misp-project.org/openapi/#tag/Workflows/operation/getWorkflows",
    namespace="tools.misp",
    secrets=[misp_secret],
)
async def list_misp_workflows(
    base_url: Annotated[str, Field(..., description="Base URL of the MISP instance")],
    verify_ssl: Annotated[bool, Field(True, description="If False, disables SSL verification.")] = True,
) -> dict:
    headers = {
        "Authorization": secrets.get("MISP_API_KEY"),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    url = f"{base_url.rstrip('/')}/workflows/index"
    
    async with httpx.AsyncClient(verify=verify_ssl) as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        return response.json()

@registry.register(
    default_title="Create MISP Workflow",
    description="Create a new workflow in MISP.",
    display_group="MISP",
    doc_url="https://www.misp-project.org/openapi/#tag/Workflows/operation/addWorkflow",
    namespace="tools.misp",
    secrets=[misp_secret],
)
async def create_misp_workflow(
    base_url: Annotated[str, Field(..., description="Base URL of the MISP instance")],
    name: Annotated[str, Field(..., description="Name of the workflow")],
    trigger_type: Annotated[str, Field(..., description="Type of trigger (event, attribute, etc.)")],
    conditions: Annotated[list[dict], Field(..., description="List of conditions for the workflow")],
    actions: Annotated[list[dict], Field(..., description="List of actions to execute")],
    description: Annotated[Optional[str], Field(None, description="Description of the workflow")] = None,
    verify_ssl: Annotated[bool, Field(True, description="If False, disables SSL verification.")] = True,
) -> dict:
    headers = {
        "Authorization": secrets.get("MISP_API_KEY"),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    url = f"{base_url.rstrip('/')}/workflows/add"
    data = {
        "Workflow": {
            "name": name,
            "trigger_type": trigger_type,
            "conditions": conditions,
            "actions": actions,
        }
    }

    if description:
        data["Workflow"]["description"] = description

    async with httpx.AsyncClient(verify=verify_ssl) as client:
        response = await client.post(url, headers=headers, json=data)
        response.raise_for_status()
        return response.json()

@registry.register(
    default_title="Execute MISP Workflow",
    description="Manually execute a workflow in MISP.",
    display_group="MISP",
    doc_url="https://www.misp-project.org/openapi/#tag/Workflows/operation/executeWorkflow",
    namespace="tools.misp",
    secrets=[misp_secret],
)
async def execute_misp_workflow(
    base_url: Annotated[str, Field(..., description="Base URL of the MISP instance")],
    workflow_id: Annotated[int, Field(..., description="ID of the workflow to execute")],
    event_id: Annotated[Optional[int], Field(None, description="ID of the event to execute the workflow on")] = None,
    attribute_id: Annotated[Optional[int], Field(None, description="ID of the attribute to execute the workflow on")] = None,
    verify_ssl: Annotated[bool, Field(True, description="If False, disables SSL verification.")] = True,
) -> dict:
    headers = {
        "Authorization": secrets.get("MISP_API_KEY"),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    url = f"{base_url.rstrip('/')}/workflows/execute/{workflow_id}"
    data = {}
    
    if event_id is not None:
        data["event_id"] = event_id
    if attribute_id is not None:
        data["attribute_id"] = attribute_id

    async with httpx.AsyncClient(verify=verify_ssl) as client:
        response = await client.post(url, headers=headers, json=data)
        response.raise_for_status()
        return response.json() 