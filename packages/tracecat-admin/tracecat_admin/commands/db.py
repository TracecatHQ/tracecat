"""Database commands."""

from __future__ import annotations

import importlib

import typer
from rich.markup import escape

from tracecat_admin.output import print_error

app = typer.Typer(no_args_is_help=True)

ROLES_MODULE = "tracecat.db.roles"


@app.command(
    "roles",
    # Every argument and option, --help included, goes to the role script.
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
    add_help_option=False,
)
def roles(ctx: typer.Context) -> None:
    """Database role setup: bootstrap, upgrade, status, migrate (see --help)."""
    try:
        # Imported on use: the rest of the CLI runs without the tracecat package.
        module = importlib.import_module(ROLES_MODULE)
    except ModuleNotFoundError as exc:
        # Only a missing tracecat package; anything else it imports failing
        # is a real error, so let it surface.
        if exc.name not in ("tracecat", "tracecat.db", ROLES_MODULE):
            raise
        print_error(
            escape(
                "tracecat db roles needs the tracecat package: run it from the "
                "Tracecat image, or install tracecat-admin[bootstrap]."
            )
        )
        raise typer.Exit(1) from None
    raise typer.Exit(module.main(ctx.args))
