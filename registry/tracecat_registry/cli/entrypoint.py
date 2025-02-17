import warnings

import typer
from tracecat.logger import logger

from . import validate

# Silence loggers
logger.remove()

# Filter out Pydantic serializer warnings
warnings.filterwarnings("ignore", message="Pydantic serializer warning*")


app = typer.Typer(no_args_is_help=True, pretty_exceptions_show_locals=False)


def version_callback(value: bool):
    if value:
        from tracecat_registry import __version__

        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def tc(
    ctx: typer.Context,
    version: bool = typer.Option(None, "--version", callback=version_callback),
):
    pass


app.add_typer(validate.app, name="validate")


if __name__ == "__main__":
    typer.run(app)
