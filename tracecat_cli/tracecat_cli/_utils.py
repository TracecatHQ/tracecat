from pathlib import Path

import httpx
import orjson
import rich
import typer
from rich.table import Table

from ._config import config


def user_client() -> httpx.AsyncClient:
    """Returns an asynchronous httpx client with the user's JWT token."""
    return httpx.AsyncClient(
        headers={"Authorization": f"Bearer {config.jwt_token}"},
        base_url=config.api_url,
    )


def user_client_sync() -> httpx.Client:
    """Returns a synchronous httpx client with the user's JWT token."""
    return httpx.Client(
        headers={"Authorization": f"Bearer {config.jwt_token}"},
        base_url=config.api_url,
    )


def dynamic_table(data: list[dict[str, str]], title: str) -> Table:
    # Dynamically add columns based on the keys of the JSON objects
    table = Table(title=title)
    if data:
        for key in data[0].keys():
            table.add_column(key.capitalize())

        # Add rows to the table
        for item in data:
            table.add_row(*[str(value) for value in item.values()])
    return table


def read_input(data: str) -> dict[str, str]:
    """Read data from a file or JSON string.

    If the data starts with '@', it is treated as a file path.
    Else it is treated as a JSON string.
    """
    if data[0] == "@":
        p = Path(data[1:])
        if not p.exists():
            raise typer.BadParameter(f"File {p} does not exist")
        if p.suffix != ".json":
            raise typer.BadParameter(f"File {p} is not a JSON file")
        with p.open() as f:
            data = f.read()
    try:
        return orjson.loads(data)
    except orjson.JSONDecodeError as e:
        raise typer.BadParameter(f"Invalid JSON: {e}") from e


def handle_response(res: httpx.Response) -> None:
    if res.status_code == 422:
        rich.print("[red]Data validation error[/red]")
        rich.print(res.json())
        raise typer.Exit()
    res.raise_for_status()
