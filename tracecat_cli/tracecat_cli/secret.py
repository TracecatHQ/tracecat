import asyncio
from typing import TypedDict

import orjson
import rich
import typer
from rich.console import Console

from ._utils import dynamic_table, user_client

app = typer.Typer(no_args_is_help=True, help="Manage secrets.")


class SecretKeyValue(TypedDict):
    key: str
    value: str


def keyvalues_from_string(keyvalues: list[str]) -> list[SecretKeyValue]:
    keyvalues = []
    for kv in keyvalues:
        key, value = kv.split("=", 1)
        keyvalues.append({"key": key, "value": value})
    return keyvalues


async def create_secret(secret_name: str, keyvalues: list[str]):
    """
    Create a secret.

    Args:
        secret_name (str): Secret name, can have multiple key-value pairs.
        keyvalues (list[str]): Space-separated KEY-VALUE items.
    """
    params = {"name": secret_name, "keys": keyvalues_from_string(keyvalues)}
    async with user_client() as client:
        res = await client.put("/secrets", content=orjson.dumps(params))
        res.raise_for_status()
    return res.json()


async def delete_secret(secret_name: str):
    """
    Delete a secret.

    Args:
        secret_name (str): Secret name.
    """
    async with user_client() as client:
        res = await client.delete(f"/secrets/{secret_name}")
        res.raise_for_status()
    return res.json()


async def list_secrets():
    """
    List all secrets.

    Returns:
        dict: JSON response containing the list of secrets.
    """
    async with user_client() as client:
        res = await client.get("/secrets")
        res.raise_for_status()
    return res.json()


@app.command(no_args_is_help=True, help="Create a secret.")
def create(
    secret_name: str = typer.Argument(
        ..., help="Secret name, can have multiple key-value pairs"
    ),
    keyvalues: list[str] = typer.Argument(
        ...,
        help="Space-separated KEY-VALUE items, e.g. `KEY1=VAL1 KEY2=VAL2 ...`.",
    ),
):
    """
    Create a secret.

    Args:
        secret_name (str): Secret name, can have multiple key-value pairs.
        keyvalues (list[str]): Space-separated KEY-VALUE items.
    """
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
    if not typer.confirm(f"Are you sure you want to delete {secret_name!r}"):
        rich.print("Aborted")
        return
    asyncio.run(delete_secret(secret_name))
    rich.print("[green]Secret deleted successfully![/green]")
