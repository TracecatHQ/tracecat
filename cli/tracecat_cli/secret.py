from typing import TypedDict

import orjson
import rich
import typer
from rich.console import Console

from .client import Client
from .utils import dynamic_table, pprint_json

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
        res = client.post("/secrets", content=orjson.dumps(params))
        Client.handle_response(res)
    rich.print("[green]Secret created successfully![/green]")


@app.command(name="list", help="List all secrets")
def list_secrets(
    as_json: bool = typer.Option(False, "--json", help="Display as JSON"),
):
    """
    List all secrets.
    """
    with Client() as client:
        res = client.get("/secrets")
        result = Client.handle_response(res)

    if as_json:
        pprint_json(result)
    elif not result:
        rich.print("[cyan]No secrets found[/cyan]")
    else:
        table = dynamic_table(title="Secrets", data=result)
        Console().print(table)


@app.command(no_args_is_help=True, help="Delete secrets.")
def delete(
    secret_names: list[str] = typer.Argument(
        ..., help="List of secret names to delete"
    ),
):
    """
    Delete a secret.

    Args:
        secret_name (str): Secret name.
    """
    if not typer.confirm(f"Are you sure you want to delete {secret_names!r}"):
        rich.print("Aborted")
        return

    try:
        with Client() as client:
            for name in secret_names:
                get_response = client.get(f"/secrets/{name}")
                secret = Client.handle_response(get_response)
                del_response = client.delete(f"/secrets/{secret['id']}")
                del_response.raise_for_status()
        rich.print("[green]Secret deleted successfully![/green]")
    except Exception as e:
        rich.print(f"[red]Error: {e}[/red]")
        return


@app.command(no_args_is_help=True, help="Update a secret.")
def update(
    secret_name: str = typer.Argument(..., help="Secret name"),
    keyvalues: list[str] = typer.Argument(
        ..., help="Space-separated KEY-VALUE items, e.g. `KEY1=VAL1 KEY2=VAL2 ...`."
    ),
):
    """
    Update a secret.

    Args:
        secret_name (str): Secret name.
        keyvalues (list[str]): Space-separated KEY-VALUE items.
    """
    params = {"keys": keyvalues_from_str(keyvalues)}
    with Client() as client:
        res = client.post(f"/secrets/{secret_name}", content=orjson.dumps(params))
        Client.handle_response(res)
    rich.print("[green]Secret updated successfully![/green]")
