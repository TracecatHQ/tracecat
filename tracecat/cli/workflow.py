import asyncio
import json
from pathlib import Path

import httpx
import orjson
import rich
import typer
from rich.console import Console

from tracecat.types.api import WebhookResponse

from ._utils import dynamic_table, user_client

app = typer.Typer(no_args_is_help=True, help="Manage workflows.")


async def _commit_workflow(yaml_path: Path, workflow_id: str):
    """Commit a workflow definition to the database."""

    kwargs = {}
    if yaml_path:
        with yaml_path.open() as f:
            yaml_content = f.read()
        kwargs["files"] = {
            "yaml_file": (yaml_path.name, yaml_content, "application/yaml")
        }

    async with user_client() as client:
        res = await client.post(f"/workflows/{workflow_id}/commit", **kwargs)
        res.raise_for_status()


async def _run_workflow(workflow_id: str, payload: dict[str, str] | None = None):
    async with user_client() as client:
        # Get the webhook url
        res = await client.get(f"/workflows/{workflow_id}/webhook")
        res.raise_for_status()
        webhooks = WebhookResponse.model_validate(res.json())
    async with httpx.AsyncClient() as client:
        res = await client.post(
            webhooks.url, content=orjson.dumps(payload) if payload else None
        )
        try:
            res.raise_for_status()
            rich.print(res.json())
        except json.JSONDecodeError:
            rich.print(res.text)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                rich.print(
                    "Webhook not found. Either you entered it incorrectly or haven't exposed it yet (through ngrok)."
                )


async def _create_workflow(
    title: str | None = None,
    description: str | None = None,
    activate_workflow: bool = False,
    activate_webhook: bool = False,
):
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
        result = res.json()
        rich.print(result)

        if activate_workflow:
            await _activate_workflow(result["id"], activate_webhook)


async def _activate_workflow(workflow_id: str, with_webhook: bool = False):
    async with user_client() as client:
        res = await client.patch(
            f"/workflows/{workflow_id}", content=orjson.dumps({"status": "online"})
        )
        res.raise_for_status()
        if with_webhook:
            res = await client.patch(
                f"/workflows/{workflow_id}/webhook",
                content=orjson.dumps({"status": "online"}),
            )
            res.raise_for_status()


async def _list_workflows():
    async with user_client() as client:
        res = await client.get("/workflows")
        res.raise_for_status()
    return dynamic_table(res.json(), "Workfows")


@app.command(help="Create a workflow")
def create(
    title: str = typer.Option(None, "--title", "-t", help="Title of the workflow"),
    description: str = typer.Option(None, "--description", "-d", help="Description"),
    activate_workflow: bool = typer.Option(
        False, "--activate", help="Activate the workflow"
    ),
    activate_webhook: bool = typer.Option(
        False, "--webhook", help="Activate the webhook"
    ),
):
    """Create a new workflow."""
    rich.print("Creating a new workflow")
    asyncio.run(
        _create_workflow(title, description, activate_workflow, activate_webhook)
    )


@app.command(help="Commit a workflow definition")
def commit(
    workflow_id: str = typer.Argument(..., help="ID of the workflow"),
    file: Path = typer.Option(
        None, "--file", "-f", help="Path to the workflow definition YAML file"
    ),
):
    """Commit a workflow definition to the database."""
    asyncio.run(_commit_workflow(file, workflow_id))
    rich.print(f"Upserted workflow definition for {workflow_id!r}")


@app.command(help="List all workflow definitions")
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


@app.command(help="Activate a workflow", no_args_is_help=True)
def up(
    workflow_id: str = typer.Argument(..., help="ID of the workflow to activate."),
    with_webhook: bool = typer.Option(
        False, "--webhook", help="Activate its webhook as well."
    ),
):
    """Triggers a webhook to run a workflow."""
    rich.print(f"Activating workflow {workflow_id!r}")
    asyncio.run(_activate_workflow(workflow_id, with_webhook))
