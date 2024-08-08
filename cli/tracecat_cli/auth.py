from __future__ import annotations

import httpx
import rich
import typer

from ._client import Client
from ._config import config
from ._utils import delete_cookies, write_cookies

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
    write_cookies(response.cookies, config.cookies_path)

    rich.print(
        f"[green]Login successful. Cookies saved to {config.cookies_path}[/green]"
    )


@app.command(help="Get the current user")
def whoami():
    """Get the current user."""
    with Client() as client:
        response = client.get("/users/me")
        response.raise_for_status()
        rich.print(response.json())


@app.command(
    help="Login to Tracecat. If no username or password is provided, the user will be prompted."
)
def logout():
    """Logout from Tracecat."""
    with Client() as client:
        response = client.post("/auth/logout")
        response.raise_for_status()
    # Convert cookies to a dictionary
    delete_cookies(config.cookies_path)
    rich.print("[green]Logout successful[/green]")
