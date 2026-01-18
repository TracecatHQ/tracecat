"""Authentication commands for tracecat-admin CLI."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from functools import wraps
from typing import Annotated, Any

import httpx
import typer

from tracecat_admin.client import AdminClient, AdminClientError
from tracecat_admin.config import clear_cookies, get_config, save_cookies
from tracecat_admin.output import print_error, print_success

app = typer.Typer(no_args_is_help=True)


def async_command[F: Callable[..., Any]](func: F) -> F:
    """Decorator to run async functions in typer commands."""

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return asyncio.run(func(*args, **kwargs))

    return wrapper  # type: ignore[return-value]


@app.command()
def login(
    username: Annotated[
        str,
        typer.Option(
            "-u",
            "--username",
            prompt=True,
            help="Username (email address)",
        ),
    ],
    password: Annotated[
        str,
        typer.Option(
            "-p",
            "--password",
            prompt=True,
            hide_input=True,
            help="Password",
        ),
    ],
) -> None:
    """Login to Tracecat."""
    config = get_config()
    try:
        with httpx.Client(base_url=config.api_url, timeout=30.0) as client:
            response = client.post(
                "/auth/login",
                data={"username": username, "password": password},
            )
            response.raise_for_status()
            save_cookies(response.cookies)
        print_success("Login successful")
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 400:
            try:
                detail = e.response.json().get("detail", "Invalid credentials")
            except Exception:
                detail = "Invalid credentials"
            print_error(detail)
        elif e.response.status_code == 404:
            print_error(
                "Login endpoint not found. Ensure basic auth is enabled "
                "(TRACECAT__AUTH_TYPES must include 'basic')"
            )
        elif e.response.status_code == 422:
            print_error(f"Validation error: {e.response.text}")
        else:
            print_error(f"Login failed ({e.response.status_code}): {e.response.text}")
        raise typer.Exit(1) from None
    except httpx.RequestError as e:
        print_error(f"Connection error: {e}")
        raise typer.Exit(1) from None


@app.command()
def logout() -> None:
    """Logout from Tracecat."""
    clear_cookies()
    print_success("Logged out")


@app.command()
@async_command
async def whoami(
    json_output: Annotated[
        bool, typer.Option("--json", "-j", help="Output as JSON")
    ] = False,
) -> None:
    """Show current authenticated user."""
    try:
        async with AdminClient() as client:
            response = await client._request("GET", "/users/me")
            user_data = response.json()

        if json_output:
            import json

            typer.echo(json.dumps(user_data, indent=2))
        else:
            from rich.table import Table

            from tracecat_admin.output import console

            table = Table(title="Current User", show_header=False)
            table.add_column("Field", style="dim")
            table.add_column("Value")

            table.add_row("ID", str(user_data.get("id", "-")))
            table.add_row("Email", user_data.get("email", "-"))
            name = (
                " ".join(
                    filter(
                        None, [user_data.get("first_name"), user_data.get("last_name")]
                    )
                )
                or "-"
            )
            table.add_row("Name", name)
            table.add_row("Role", user_data.get("role", "-"))
            table.add_row("Superuser", "Yes" if user_data.get("is_superuser") else "No")
            table.add_row("Active", "Yes" if user_data.get("is_active") else "No")
            table.add_row("Verified", "Yes" if user_data.get("is_verified") else "No")

            console.print(table)
    except AdminClientError as e:
        print_error(str(e))
        raise typer.Exit(1) from None
