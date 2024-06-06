import asyncio
from pathlib import Path

import httpx
import orjson
import rich
import typer

from tracecat.auth import AuthenticatedAPIClient
from tracecat.dsl.workflow import DSLInput
from tracecat.types.api import WebhookResponse

from . import config

app = typer.Typer(no_args_is_help=True)


async def _upsert_workflow_definition(yaml_path: Path, workflow_id: str):
    """Bypass /workflows/{workflow_id}/commit endpoint and directly upsert the definition."""
    defn_content = DSLInput.from_yaml(yaml_path)

    async with AuthenticatedAPIClient(role=config.ROLE) as client:
        content = orjson.dumps({"content": defn_content.model_dump()})
        res = await client.post(f"/workflows/{workflow_id}/definition", content=content)
        res.raise_for_status()


async def _run_workflow(workflow_id: str, content: dict[str, str] | None = None):
    async with AuthenticatedAPIClient(role=config.ROLE) as client:
        # Get the webhook url
        res = await client.get(f"/workflows/{workflow_id}/webhooks")
        res.raise_for_status()
        # There's only 1 webhook
        webhooks = WebhookResponse.model_validate(res.json()[0])
    async with httpx.AsyncClient() as client:
        res = await client.post(webhooks.url, content=content)
        res.raise_for_status()
        rich.print(res.json())


async def _create_workflow(title: str | None = None, description: str | None = None):
    async with AuthenticatedAPIClient(role=config.ROLE) as client:
        # Get the webhook url
        params = {}
        if title:
            params["title"] = title
        if description:
            params["description"] = description
        res = await client.post("/workflows", content=orjson.dumps(params))
        res.raise_for_status()
    rich.print("Created workflow")
    rich.print(res.json())


@app.command(help="Create a workflow")
def create(
    file: Path = typer.Option(
        ..., "--file", "-f", help="Path to the workflow definition YAML file"
    ),
):
    """Create a new workflow."""
    rich.print("Creating a new workflow")
    defn = DSLInput.from_yaml(file)
    asyncio.run(_create_workflow(defn.title, defn.description))


@app.command(help="List all workflow definitions")
def commit(
    file: Path = typer.Option(
        ..., "--file", "-f", help="Path to the workflow definition YAML file"
    ),
    workflow_id: str = typer.Option(
        ..., "--workflow-id", "-w", help="ID of the workflow"
    ),
):
    """Commit a workflow definition to the database."""
    asyncio.run(_upsert_workflow_definition(file, workflow_id))
    rich.print(f"Upserted workflow definition for {workflow_id!r}")


@app.command(help="Commit a workflow definition")
def list(
    workflow_id: str = typer.Option(None, "--workflow-id", help="ID of the workflow"),
):
    """Commit a workflow definition to the database."""
    rich.print("Listing all workflows")


@app.command(help="Run a workflow", no_args_is_help=True)
def run(
    workflow_id: str = typer.Argument(..., help="ID of the workflow to run"),
    data: str = typer.Option(None, "--data", "-d", help="JSON Payload to send"),
):
    """Triggers a webhook to run a workflow."""
    rich.print(f"Running workflow {workflow_id!r}")
    asyncio.run(_run_workflow(workflow_id, orjson.loads(data) if data else None))
