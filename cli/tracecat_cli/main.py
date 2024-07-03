import typer
from dotenv import find_dotenv, load_dotenv

from . import dev, schedule, secret, workflow

load_dotenv(find_dotenv())
app = typer.Typer(no_args_is_help=True, pretty_exceptions_show_locals=False)


def version_callback(value: bool):
    if value:
        from tracecat import __version__

        typer.echo(f"Tracecat version: {__version__}")
        raise typer.Exit()


@app.callback()
def tracecat(
    ctx: typer.Context,
    version: bool = typer.Option(None, "--version", callback=version_callback),
):
    pass


app.add_typer(workflow.app, name="workflow")
app.add_typer(dev.app, name="dev")
app.add_typer(secret.app, name="secret")
app.add_typer(schedule.app, name="schedule")

if __name__ == "__main__":
    typer.run(app)
