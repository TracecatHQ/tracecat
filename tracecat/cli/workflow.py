import asyncio
from pathlib import Path

import httpx
import orjson
import rich
import typer
from rich.console import Console

from tracecat.types.api import WebhookResponse

from ._config import config
from ._utils import dynamic_table

app = typer.Typer(no_args_is_help=True, help="Manage workflows.")


def user_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={"Authorization": f"Bearer {config.jwt_token}"},
        base_url=config.api_url,
    )


async def _commit_workflow(yaml_path: Path, workflow_id: str):
    """Commit a workflow definition to the database."""

    with yaml_path.open() as f:
        yaml_content = f.read()

    async with user_client() as client:
        res = await client.post(
            f"/workflows/{workflow_id}/commit",
            files={"yaml_file": (yaml_path.name, yaml_content, "application/yaml")},
        )
        res.raise_for_status()


async def _run_workflow(workflow_id: str, content: dict[str, str] | None = None):
    async with user_client() as client:
        # Get the webhook url
        res = await client.get(f"/workflows/{workflow_id}/webhook")
        res.raise_for_status()
        webhooks = WebhookResponse.model_validate(res.json())
    async with httpx.AsyncClient() as client:
        res = await client.post(webhooks.url, content=content)
        res.raise_for_status()
        rich.print(res.json())


async def _create_workflow(title: str | None = None, description: str | None = None):
    async with user_client() as client:
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


async def _list_workflows():
    async with user_client() as client:
        res = await client.get("/workflows")
        res.raise_for_status()
    return dynamic_table(res.json(), "Workfows")


@app.command(help="Create a workflow")
def create(
    title: str = typer.Option(None, "--title", "-t", help="Title of the workflow"),
    description: str = typer.Option(None, "--description", "-d", help="Description"),
):
    """Create a new workflow."""
    rich.print("Creating a new workflow")
    asyncio.run(_create_workflow(title, description))


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
    asyncio.run(_commit_workflow(file, workflow_id))
    rich.print(f"Upserted workflow definition for {workflow_id!r}")


@app.command(help="Commit a workflow definition")
def list():
    """Commit a workflow definition to the database."""
    rich.print("Listing all workflows")
    table = asyncio.run(_list_workflows())
    Console().print(table)


@app.command(help="Run a workflow", no_args_is_help=True)
def run(
    workflow_id: str = typer.Argument(..., help="ID of the workflow to run"),
    data: str = typer.Option(None, "--data", "-d", help="JSON Payload to send"),
):
    """Triggers a webhook to run a workflow."""
    rich.print(f"Running workflow {workflow_id!r}")
    asyncio.run(_run_workflow(workflow_id, orjson.loads(data) if data else None))
