import typer

app = typer.Typer(no_args_is_help=True)


@app.command(help="List all events")
def list(
    workflow_id: str = typer.Option(
        ..., "--workflow-id", "-w", help="ID of the workflow"
    ),
):
    raise typer.Exit("Not implemented")
