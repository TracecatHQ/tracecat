from pathlib import Path
from typing import Any

import orjson
import rich
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


def pprint_json(data: Any):
    """Pretty print data."""
    rich.print(orjson.dumps(data, option=orjson.OPT_INDENT_2).decode())
