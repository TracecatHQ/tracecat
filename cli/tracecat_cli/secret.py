from typing import TypedDict

import orjson
import rich
import typer
from rich.console import Console

from ._client import Client
from ._utils import dynamic_table

app = typer.Typer(no_args_is_help=True, help="Manage secrets.")


class SecretKeyValue(TypedDict):
    key: str
    value: str


def keyvalues_from_str(keyvalues: list[str]) -> list[SecretKeyValue]:
    kvs = []
    for kv in keyvalues:
        key, value = kv.split("=", 1)
        kvs.append(SecretKeyValue(key=key, value=value))
    return kvs


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
    params = {"name": secret_name, "keys": keyvalues_from_str(keyvalues)}
    with Client() as client:
        res = client.put("/secrets", content=orjson.dumps(params))
        Client.handle_response(res)
    rich.print("[green]Secret created successfully![/green]")


@app.command(help="List all secrets")
def list(
    as_json: bool = typer.Option(False, "--json", help="Display as JSON"),
):
    """
    List all secrets.
    """
    with Client() as client:
        res = client.get("/secrets")
        result = Client.handle_response(res)

    if not result:
        rich.print("[cyan]No secrets found[/cyan]")
        return

    if as_json:
        rich.print(result)
    else:
        table = dynamic_table(title="Secrets", data=result)
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
    with Client() as client:
        res = client.delete(f"/secrets/{secret_name}")
        Client.handle_response(res)
    rich.print("[green]Secret deleted successfully![/green]")
