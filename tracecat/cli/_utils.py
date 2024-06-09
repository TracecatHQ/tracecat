import httpx
from rich.table import Table

from ._config import config


def user_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
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
