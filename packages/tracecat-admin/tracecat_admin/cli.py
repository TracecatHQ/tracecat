"""Tracecat Admin CLI - Main entry point."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from functools import wraps
from typing import Any

import typer

from tracecat_admin import __version__
from tracecat_admin.commands import (
    admin,
    auth,
    migrate,
    orgs,
    registry,
    settings,
    tiers,
)

app = typer.Typer(
    name="tracecat",
    help="Tracecat Admin CLI - Platform operator tools for Tracecat.",
    no_args_is_help=True,
)


def async_command[F: Callable[..., Any]](func: F) -> F:
    """Decorator to run async functions in typer commands."""

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return asyncio.run(func(*args, **kwargs))

    return wrapper  # type: ignore[return-value]


def version_callback(value: bool) -> None:
    if value:
        typer.echo(f"tracecat-admin version {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    _version: bool = typer.Option(
        None,
        "--version",
        "-v",
        help="Show version and exit.",
        callback=version_callback,
        is_eager=True,
    ),
) -> None:
    """Tracecat Admin CLI - Platform operator tools."""


# Register command groups
app.add_typer(auth.app, name="auth", help="Authentication commands.")
app.add_typer(admin.app, name="admin", help="User and superuser management commands.")
app.add_typer(orgs.app, name="orgs", help="Organization management commands.")
app.add_typer(registry.app, name="registry", help="Registry management commands.")
app.add_typer(settings.app, name="settings", help="Platform settings commands.")
app.add_typer(migrate.app, name="migrate", help="Database migration commands.")
app.add_typer(tiers.app, name="tiers", help="Tier management commands.")


if __name__ == "__main__":
    app()
