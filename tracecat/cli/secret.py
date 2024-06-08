import asyncio

import rich
import typer
from rich.console import Console

from tracecat.auth.clients import AuthenticatedAPIClient
from tracecat.types.api import CreateSecretParams

from . import config
from ._utils import dynamic_table

app = typer.Typer(no_args_is_help=True, help="Manage secrets.")


async def create_secret(secret_name: str, keyvalues: list[str]):
    params = CreateSecretParams.from_strings(secret_name, keyvalues)
    async with AuthenticatedAPIClient(role=config.ROLE) as client:
        res = await client.put(
            "/secrets", content=params.model_dump_json(exclude_unset=True).encode()
        )
        res.raise_for_status()


async def list_secrets():
    async with AuthenticatedAPIClient(role=config.ROLE) as client:
        res = await client.get("/secrets")
        res.raise_for_status()
    return res.json()


@app.command(no_args_is_help=True, help="Create a workflow")
def create(
    secret_name: str = typer.Argument(
        ..., help="Secret name, can have multiple key-value pairs"
    ),
    keyvalues: list[str] = typer.Argument(..., help="Space-separated KEY-VALUE items"),
    force: bool = typer.Option(
        False, "--force", help="Overwrite the secret if it already exists"
    ),
):
    rich.print(
        f"{"Creating" if not force else "Force creating"} secret {secret_name!r}"
    )
    asyncio.run(create_secret(secret_name, keyvalues))
    rich.print("Secret created successfully!")


@app.command(help="List all workflow definitions")
def list():
    """Commit a workflow definition to the database."""
    data = asyncio.run(list_secrets())
    table = dynamic_table(title="Secrets", data=data)
    Console().print(table)
