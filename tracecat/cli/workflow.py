import asyncio
import json
from pathlib import Path

import httpx
import orjson
import rich
import typer
from rich.console import Console

from tracecat.types.api import WebhookResponse
from tracecat.types.headers import CustomHeaders

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

    try:
        async with user_client() as client:
            res = await client.post(f"/workflows/{workflow_id}/commit", **kwargs)
            res.raise_for_status()
            rich.print(f"Successfully committed to workflow {workflow_id!r}!")
    except httpx.HTTPStatusError as e:
        rich.print(f"[red]Failed to commit to workflow {workflow_id!r}![/red]")
        rich.print(e.response.json())


async def _run_workflow(
    workflow_id: str,
    payload: dict[str, str] | None = None,
    proxy: bool = False,
    test: bool = False,
):
    async with user_client() as client:
        # Get the webhook url
        res = await client.get(f"/workflows/{workflow_id}/webhook")
        res.raise_for_status()
        webhook = WebhookResponse.model_validate(res.json())
    content = orjson.dumps(payload) if payload else None
    if proxy:
        run_client = httpx.AsyncClient()
        url = webhook.url
    else:
        run_client = user_client()
        url = f"/webhooks/{workflow_id}/{webhook.secret}"

    async with run_client as client:
        res = await client.post(
            url,
            content=content,
            headers={CustomHeaders.ENABLE_RUNTIME_TESTS: "true"} if test else None,
        )
        try:
            res.raise_for_status()
            rich.print(res.json())
        except json.JSONDecodeError:
            rich.print(res.text)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                rich.print(
                    "Webhook or workflow not found. Are your workflow ID and webhook URL correct?."
                )
            elif e.response.status_code == 400:
                rich.print(
                    f"{e.response.text}. If the webhook or workflow is offline, "
                    "run `tracecat workflow up <workflow_id> --webhook` first."
                )
            else:
                rich.print(e.response.json())


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
        return res.json()


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
    return dynamic_table(res.json(), "Workflows")


async def _get_cases(workflow_id: str):
    async with user_client() as client:
        res = await client.get(f"/workflows/{workflow_id}/cases")
        res.raise_for_status()
    return res.json()


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
    defn_file: Path = typer.Option(
        None, "--commit", "-c", help="Create with workflow definition"
    ),
):
    """Create a new workflow."""
    rich.print("Creating a new workflow")

    async def tasks():
        result = await _create_workflow(title=title, description=description)
        rich.print(result)
        if activate_workflow:
            await _activate_workflow(result["id"], activate_webhook)
        if defn_file:
            await _commit_workflow(defn_file, result["id"])

    asyncio.run(tasks())


@app.command(help="Commit a workflow definition")
def commit(
    workflow_id: str = typer.Argument(..., help="ID of the workflow"),
    file: Path = typer.Option(
        None, "--file", "-f", help="Path to the workflow definition YAML file"
    ),
):
    """Commit a workflow definition to the database."""
    asyncio.run(_commit_workflow(file, workflow_id))


@app.command(name="list", help="List all workflow definitions")
def list_workflows():
    """Commit a workflow definition to the database."""
    rich.print("Listing all workflows")
    table = asyncio.run(_list_workflows())
    Console().print(table)


@app.command(help="Run a workflow", no_args_is_help=True)
def run(
    workflow_id: str = typer.Argument(..., help="ID of the workflow to run"),
    data: str = typer.Option(None, "--data", "-d", help="JSON Payload to send"),
    proxy: bool = typer.Option(
        False,
        "--proxy",
        help="If set, run the workflow through the external-facing webhook",
    ),
    test: bool = typer.Option(
        False, "--test", help="If set, run the workflow with runtime action tests"
    ),
):
    """Triggers a webhook to run a workflow."""
    rich.print(f"Running workflow {workflow_id!r} {"proxied" if proxy else 'directly'}")
    if data[0] == "@":
        p = Path(data[1:])
        if not p.exists():
            raise typer.BadParameter(f"File {p} does not exist")
        if p.suffix != ".json":
            raise typer.BadParameter(f"File {p} is not a JSON file")
        with p.open() as f:
            data = f.read()

    asyncio.run(
        _run_workflow(
            workflow_id,
            payload=orjson.loads(data) if data else None,
            proxy=proxy,
            test=test,
        )
    )


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


@app.command(help="List cases.", no_args_is_help=True)
def cases(
    workflow_id: str = typer.Argument(..., help="ID of the workflow to get cases."),
    table: bool = typer.Option(False, "--table", "-t", help="Display as table"),
):
    """Triggers a webhook to run a workflow."""
    results = asyncio.run(_get_cases(workflow_id))
    if table:
        Console().print(dynamic_table(results, "Cases"))
    else:
        rich.print(results)


@app.command(help="Delete a workflow")
def delete(
    workflow_ids: list[str] = typer.Argument(..., help="IDs of the workflow to delete"),
):
    """Delete workflows"""

    async def _delete_workflows(workflow_ids: list[str]):
        delete = typer.confirm(f"Are you sure you want to delete {workflow_ids!r}")
        if not delete:
            rich.print("Aborted")
            return
        async with user_client() as client, asyncio.TaskGroup() as tg:
            for workflow_id in workflow_ids:
                tg.create_task(client.delete(f"/workflows/{workflow_id}"))

        rich.print("Deleted workflows")

    asyncio.run(_delete_workflows(workflow_ids))


@app.command(help="Inspect a workflow")
def inspect(
    workflow_id: str = typer.Argument(..., help="ID of the workflow to inspect"),
):
    """Inspect a workflow"""

    async def _inspect_workflow():
        async with user_client() as client:
            res = await client.get(f"/workflows/{workflow_id}")
            res.raise_for_status()
            return res.json()

    result = asyncio.run(_inspect_workflow())
    rich.print(result)
