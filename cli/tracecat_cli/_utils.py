import json
from pathlib import Path

import httpx
import orjson
import typer
from rich.table import Table


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


def write_cookies(cookies: httpx.Cookies, cookies_path: Path) -> None:
    """Write cookies to file."""
    cookies_dict = dict(cookies)

    # Overwrite the cookies file
    with cookies_path.open(mode="w") as f:
        json.dump(cookies_dict, f)


def read_cookies(cookies_path: Path) -> httpx.Cookies:
    """Read cookies from file."""
    try:
        with cookies_path.open() as f:
            cookies_dict = json.load(f)
        return httpx.Cookies(cookies_dict)
    except (FileNotFoundError, json.JSONDecodeError):
        return httpx.Cookies()


def delete_cookies(cookies_path: Path) -> None:
    """Delete cookies file."""
    cookies_path.unlink(missing_ok=True)
