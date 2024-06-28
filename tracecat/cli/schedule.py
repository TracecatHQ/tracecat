from __future__ import annotations

import asyncio

import rich
import typer
from rich.console import Console

from tracecat.types.api import CreateScheduleParams, UpdateScheduleParams

from ._utils import (
    dynamic_table,
    handle_response,
    read_input,
    user_client,
    user_client_sync,
)

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

    params = CreateScheduleParams(
        workflow_id=workflow_id,
        every=every,
        offset=offset,
        inputs=inputs,
    )

    with user_client_sync() as client:
        res = client.post(
            "/schedules",
            json=params.model_dump(exclude_unset=True, exclude_none=True, mode="json"),
        )
        handle_response(res)

    rich.print(res.json())


@app.command(name="list", help="List all schedules")
def list_schedules(
    workflow_id: str = typer.Argument(None, help="Workflow ID"),
    as_table: bool = typer.Option(False, "--table", "-t", help="Display as table"),
):
    """List all schedules."""

    params = {}
    if workflow_id:
        params["workflow_id"] = workflow_id
    with user_client_sync() as client:
        res = client.get("/schedules", params=params)
        handle_response(res)

    result = res.json()
    if as_table:
        table = dynamic_table(result, "Schedules")
        Console().print(table)
    else:
        rich.print(result)


@app.command(help="Delete schedules", no_args_is_help=True)
def delete(
    schedule_ids: list[str] = typer.Argument(
        ..., help="IDs of the schedules to delete"
    ),
):
    """Delete schedules"""

    delete = typer.confirm(f"Are you sure you want to delete {schedule_ids!r}")
    if not delete:
        rich.print("Aborted")
        return

    async def _delete():
        async with user_client() as client, asyncio.TaskGroup() as tg:
            for sch_id in schedule_ids:
                tg.create_task(client.delete(f"/schedules/{sch_id}"))

    asyncio.run(_delete())


@app.command(help="Update a schedule", no_args_is_help=True)
def update(
    schedule_id: str = typer.Argument(..., help="ID of the schedule to update."),
    inputs: str = typer.Option(
        None, "--data", "-d", help="JSON Payload to send (trigger context)"
    ),
    every: str = typer.Option(
        None, "--every", "-e", help="Interval at which the schedule should run"
    ),
    offset: str = typer.Option(
        None, "--offset", "-o", help="Offset from the start of the interval"
    ),
):
    """Update a schedule"""

    params = UpdateScheduleParams(
        inputs=read_input(inputs) if inputs else None,
        every=every,
        offset=offset,
    )
    with user_client_sync() as client:
        res = client.post(
            f"/schedules/{schedule_id}",
            json=params.model_dump(exclude_unset=True, exclude_none=True),
        )
        handle_response(res)
    rich.print(res.json())


@app.command(help="Inspect a schedule", no_args_is_help=True)
def inspect(
    schedule_id: str = typer.Argument(..., help="ID of the schedule to inspect"),
):
    """Inspect a schedule"""

    with user_client_sync() as client:
        res = client.get(f"/schedules/{schedule_id}")
        handle_response(res)
    rich.print(res.json())
