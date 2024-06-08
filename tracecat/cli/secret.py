import asyncio

import rich
import typer
from rich.console import Console

from tracecat.auth.clients import AuthenticatedAPIClient
from tracecat.types.api import CreateSecretParams

from ._config import config
from ._utils import dynamic_table

app = typer.Typer(no_args_is_help=True, help="Manage secrets.")


async def create_secret(secret_name: str, keyvalues: list[str]):
    """
    Create a secret.

    Args:
        secret_name (str): Secret name, can have multiple key-value pairs.
        keyvalues (list[str]): Space-separated KEY-VALUE items.
    """
    params = CreateSecretParams.from_strings(secret_name, keyvalues)
    async with AuthenticatedAPIClient(role=config.role) as client:
        res = await client.put(
            "/secrets", content=params.model_dump_json(exclude_unset=True).encode()
        )
        res.raise_for_status()


async def delete_secret(secret_name: str):
    """
    Delete a secret.

    Args:
        secret_name (str): Secret name.
    """
    async with AuthenticatedAPIClient(role=config.role) as client:
        res = await client.delete(f"/secrets/{secret_name}")
        res.raise_for_status()


async def list_secrets():
    """
    List all secrets.

    Returns:
        dict: JSON response containing the list of secrets.
    """
    async with AuthenticatedAPIClient(role=config.role) as client:
        res = await client.get("/secrets")
        res.raise_for_status()
    return res.json()


@app.command(no_args_is_help=True, help="Create a secret.")
def create(
    secret_name: str = typer.Argument(
        ..., help="Secret name, can have multiple key-value pairs"
    ),
    keyvalues: list[str] = typer.Argument(..., help="Space-separated KEY-VALUE items"),
):
    """
    Create a secret.

    Args:
        secret_name (str): Secret name, can have multiple key-value pairs.
        keyvalues (list[str]): Space-separated KEY-VALUE items.
    """
    rich.print(f"Creating secret {secret_name!r}")
    asyncio.run(create_secret(secret_name, keyvalues))
    rich.print("[green]Secret created successfully![/green]")


@app.command(help="List all secrets")
def list():
    """
    List all secrets.
    """
    data = asyncio.run(list_secrets())
    table = dynamic_table(title="Secrets", data=data)
    Console().print(table)


@app.command(no_args_is_help=True, help="Delete a secret.")
def delete(secret_name: str = typer.Argument(..., help="Secret name")):
    """
    Delete a secret.

    Args:
        secret_name (str): Secret name.
    """
    rich.print(f"Deleting secret {secret_name!r}")
    asyncio.run(delete_secret(secret_name))
    rich.print("[green]Secret deleted successfully![/green]")
