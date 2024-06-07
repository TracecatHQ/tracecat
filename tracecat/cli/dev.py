import asyncio
from typing import Any

import orjson
import rich
import typer

from tracecat.auth.clients import AuthenticatedAPIClient

from . import config

app = typer.Typer(no_args_is_help=True)


async def hit_api_endpoint(
    method: str, endpoint: str, payload: dict[str, str] | None
) -> dict[str, Any]:
    async with AuthenticatedAPIClient(role=config.ROLE) as client:
        content = orjson.dumps(payload) if payload else None
        res = await client.request(method=method, url=endpoint, content=content)
        res.raise_for_status()
    return res.json()


@app.command(help="Hit the API endpoint with an authenticated service client")
def api(
    endpoint: str = typer.Argument(..., help="Endpoint to hit"),
    method: str = typer.Option("GET", "-X", help="HTTP Method"),
    data: str = typer.Option(None, "--data", "-d", help="JSON Payload to send"),
):
    """Commit a workflow definition to the database."""
    payload = orjson.loads(data) if data else None
    result = asyncio.run(hit_api_endpoint(method, endpoint, payload))
    rich.print("Hit the endpoint successfully!")
    rich.print(result, len(result))
