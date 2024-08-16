import uuid

import rich
import typer

from .client import Client
from .config import manager
from .utils import dynamic_table, pprint_json

app = typer.Typer(no_args_is_help=True, help="Manage workspaces.")


@app.command(name="list", help="List workspaces")
def list_workspaces(
    as_json: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """List all workspaces."""
    with Client() as client:
        res = client.get("/workspaces")
        result = Client.handle_response(res)

    if as_json:
        pprint_json(result)
    else:
        table = dynamic_table(result, "Workspaces")
        rich.print(table)


@app.command(help="Create a workspace")
def create(
    name: str = typer.Argument(..., help="Name of the workspace"),
):
    """Create a new workspace."""
    with Client() as client:
        res = client.post("/workspaces", json={"name": name})
        result = Client.handle_response(res)

    pprint_json(result)


@app.command(help="Delete a workspace")
def delete(
    workspace_id: str = typer.Argument(..., help="ID of the workspace"),
):
    """Delete a workspace."""
    with Client() as client:
        res = client.delete(f"/workspaces/{workspace_id}")
        result = Client.handle_response(res)

    pprint_json(result)


@app.command(help="Select a workspace")
def checkout(
    workspace_id: uuid.UUID = typer.Option(None, "--id", help="ID of the workspace"),
    workspace_name: str = typer.Option(None, "--name", help="Name of the workspace"),
):
    """Checkout a workspace."""
    # Provide one other the other, but not both
    if not (bool(workspace_id) ^ bool(workspace_name)):
        raise typer.BadParameter("Must provide either --id or --name but not both")
    if workspace_id:
        with Client() as client:
            res = client.get(f"/workspaces/{workspace_id}")
            result = Client.handle_response(res)
    if workspace_name:
        with Client() as client:
            res = client.get("/workspaces/search", params={"name": workspace_name})
            result = Client.handle_response(res)
            workspace_data = result[0]
    workspace = manager.set_workspace(
        workspace_id=workspace_data["id"], workspace_name=workspace_data["name"]
    )
    pprint_json(workspace)


@app.command(help="Current workspace")
def current():
    """View the current workspace."""
    workspace = manager.get_workspace()
    if not workspace:
        rich.print("[red]No workspace selected[/red]")
        raise typer.Exit()
    with Client() as client:
        res = client.get(f"/workspaces/{workspace['id']}")
        result = Client.handle_response(res)
    pprint_json(result)


@app.command(help="Reset the current workspace")
def reset():
    """Reset the current workspace."""
    manager.reset_workspace()
    rich.print("[green]Workspace reset[/green]")
