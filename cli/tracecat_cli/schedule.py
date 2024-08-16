from __future__ import annotations

import asyncio

import orjson
import rich
import typer
from rich.console import Console

from .client import Client
from .utils import dynamic_table, read_input

app = typer.Typer(no_args_is_help=True, help="Manage schedules.")


@app.command(help="Create a schedule", no_args_is_help=True)
def create(
    workflow_id: str = typer.Argument(..., help="Workflow ID"),
    data: str = typer.Option(
        None, "--data", "-d", help="JSON Payload to send (trigger context)"
    ),
    every: str = typer.Option(
        None, "--every", "-e", help="Interval at which the schedule should run"
    ),
    offset: str = typer.Option(
        None, "--offset", "-o", help="Offset from the start of the interval"
    ),
):
    """Create a new schedule."""

    inputs = read_input(data) if data else None

    params = {}
    if inputs:
        params["inputs"] = inputs
    if every:
        params["every"] = every
    if offset:
        params["offset"] = offset
    params["workflow_id"] = workflow_id

    with Client() as client:
        res = client.post("/schedules", json=params)
        result = Client.handle_response(res)

    rich.print(result)


@app.command(name="list", help="List all schedules")
def list_schedules(
    workflow_id: str = typer.Argument(None, help="Workflow ID"),
    as_json: bool = typer.Option(False, "--json", "-t", help="Display as JSON"),
):
    """List all schedules."""

    params = {}
    if workflow_id:
        params["workflow_id"] = workflow_id
    with Client() as client:
        res = client.get("/schedules", params=params)
        result = Client.handle_response(res)

    if as_json:
        out = orjson.dumps(result, option=orjson.OPT_INDENT_2).decode()
        rich.print(out)
    elif not result:
        rich.print("[cyan]No schedules found[/cyan]")
    else:
        table = dynamic_table(result, "Schedules")
        Console().print(table)


@app.command(help="Delete schedules", no_args_is_help=True)
def delete(
    schedule_ids: list[str] = typer.Argument(
        ..., help="IDs of the schedules to delete"
    ),
):
    """Delete schedules"""

    if not typer.confirm(f"Are you sure you want to delete {schedule_ids!r}"):
        rich.print("Aborted")
        return

    async def _delete():
        async with Client() as client, asyncio.TaskGroup() as tg:
            for sch_id in schedule_ids:
                tg.create_task(client.delete(f"/schedules/{sch_id}"))

    asyncio.run(_delete())
    rich.print("Deleted schedules successfully!")


@app.command(help="Update a schedule", no_args_is_help=True)
def update(
    schedule_id: str = typer.Argument(..., help="ID of the schedule to update."),
    inputs: str = typer.Option(
        None, "--data", "-d", help="JSON Payload to send (trigger context)"
    ),
    every: str = typer.Option(
        None, "--every", help="Interval at which the schedule should run"
    ),
    offset: str = typer.Option(
        None, "--offset", help="Offset from the start of the interval"
    ),
    online: bool = typer.Option(None, "--online", help="Set the schedule to online"),
    offline: bool = typer.Option(None, "--offline", help="Set the schedule to offline"),
):
    """Update a schedule"""
    if online and offline:
        raise typer.BadParameter("Cannot set both online and offline")

    params = {}
    if inputs:
        params["inputs"] = read_input(inputs)
    if every:
        params["every"] = every
    if offset:
        params["offset"] = offset
    if online:
        params["status"] = "online"
    if offline:
        params["status"] = "offline"

    if not params:
        raise typer.BadParameter("No parameters provided to update")
    with Client() as client:
        res = client.post(f"/schedules/{schedule_id}", json=params)
        result = Client.handle_response(res)
    rich.print(result)


@app.command(help="Inspect a schedule", no_args_is_help=True)
def inspect(
    schedule_id: str = typer.Argument(..., help="ID of the schedule to inspect"),
):
    """Inspect a schedule"""

    with Client() as client:
        res = client.get(f"/schedules/{schedule_id}")
        result = Client.handle_response(res)
    rich.print(result)
