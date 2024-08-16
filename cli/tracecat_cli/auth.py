from __future__ import annotations

import httpx
import rich
import typer

from .client import Client
from .config import config, manager
from .utils import pprint_json

app = typer.Typer(no_args_is_help=True, help="Authentication")


@app.command(
    help="Login to Tracecat. If no username or password is provided, the user will be prompted."
)
def login(
    username: str = typer.Option(
        "admin@domain.com", "--username", "-u", prompt=True, help="Username"
    ),
    password: str = typer.Option(
        "password", "--password", "-p", prompt=True, hide_input=True, help="Password"
    ),
):
    """Login to Tracecat. If no username or password is provided, the user will be prompted."""
    if not username or not password:
        rich.print("[red]Username and password are required[/red]")
        raise typer.Exit()
    with httpx.Client(base_url=config.api_url) as client:
        response = client.post(
            "/auth/login",
            data={"username": username, "password": password},
        )
        response.raise_for_status()
    # Convert cookies to a dictionary
    manager.write_cookies(response.cookies)

    rich.print(
        f"[green]Login successful. Cookies saved to {config.config_path}[/green]"
    )


@app.command(help="Get the current user")
def whoami(as_json: bool = typer.Option(False, "--json", help="Output as JSON")):
    """Get the current user."""
    with Client() as client:
        response = client.get("/users/me")
        response.raise_for_status()
        user = response.json()
    if as_json:
        pprint_json(user)
    else:
        rich.print(f"Username: {user['email']}")
        rich.print(f"First Name: {user['first_name']}")
        rich.print(f"Last Name: {user['last_name']}")


@app.command(
    help="Login to Tracecat. If no username or password is provided, the user will be prompted."
)
def logout():
    """Logout from Tracecat."""
    with Client() as client:
        response = client.post("/auth/logout")
        response.raise_for_status()
    # Convert cookies to a dictionary
    manager.delete_cookies()
    rich.print("[green]Logout successful[/green]")
