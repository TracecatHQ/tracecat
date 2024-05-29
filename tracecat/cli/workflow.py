import asyncio
from pathlib import Path

import orjson
import rich
import typer

from tracecat.auth import AuthenticatedAPIClient
from tracecat.dsl.workflow import DSLInput

from . import config

app = typer.Typer(no_args_is_help=True)


async def upsert_workflow_definition(yaml_path: Path, workflow_id: str):
    defn_content = DSLInput.from_yaml(yaml_path)

    async with AuthenticatedAPIClient(role=config.ROLE) as client:
        content = orjson.dumps({"content": defn_content.model_dump()})
        res = await client.post(f"/workflows/{workflow_id}/definition", content=content)
        res.raise_for_status()


async def run_workflow(workflow_id: str, content: dict[str, str] | None = None):
    async with AuthenticatedAPIClient(role=config.ROLE) as client:
        res = await client.post(
            f"/webhooks/{workflow_id}/{config.SECRET}", content=content
        )
        res.raise_for_status()


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
    asyncio.run(upsert_workflow_definition(file, workflow_id))
    rich.print(f"Userted workflow definition for {workflow_id!r}")


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
    asyncio.run(run_workflow(workflow_id, orjson.loads(data) if data else None))
