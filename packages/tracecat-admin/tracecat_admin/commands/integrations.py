"""Integration catalog admin commands."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from functools import wraps
from typing import Any

import typer

from tracecat_admin.output import print_error, print_success

app = typer.Typer(no_args_is_help=True)


def async_command[F: Callable[..., Any]](func: F) -> F:
    """Decorator to run async functions in typer commands."""

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return asyncio.run(func(*args, **kwargs))

    return wrapper  # type: ignore[return-value]


@app.command("seed")
@async_command
async def seed() -> None:
    """Seed the platform integration catalog from the provider registry.

    Idempotent — running again upserts metadata for known namespaces
    without duplicating. MCP servers are intentionally excluded; they
    are managed on the MCP page with their own backing table.
    """
    try:
        from tracecat.db.engine import get_async_session_context_manager
        from tracecat.integrations.catalog.seed import seed_platform_integrations
    except ImportError as exc:
        print_error(
            "Failed to import tracecat. Run this command in an environment that "
            f"has the tracecat package installed: {exc}"
        )
        raise typer.Exit(1) from exc

    try:
        async with get_async_session_context_manager() as session:
            created = await seed_platform_integrations(session)
            await session.commit()
    except Exception as exc:  # noqa: BLE001 - surface any failure to operator
        print_error(f"Seed failed: {exc}")
        raise typer.Exit(1) from exc

    print_success(f"Seeded {created} platform integration(s).")
