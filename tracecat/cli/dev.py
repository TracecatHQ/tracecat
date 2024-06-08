import asyncio
import json
from typing import Any

import orjson
import rich
import typer
import yaml

from tracecat.auth.clients import AuthenticatedAPIClient

from . import config

app = typer.Typer(no_args_is_help=True, help="Dev tools.")


async def hit_api_endpoint(
    method: str, endpoint: str, payload: dict[str, str] | None
) -> dict[str, Any]:
    async with AuthenticatedAPIClient(role=config.ROLE) as client:
        content = orjson.dumps(payload) if payload else None
        res = await client.request(method=method, url=endpoint, content=content)
        res.raise_for_status()
    return res.json()


@app.command(help="Hit the API endpoint with an authenticated service client.")
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


@app.command(help="Print the current role")
def whoami():
    rich.print(config.ROLE)


@app.command(help="Generate OpenAPI specification.")
def oas(outfile: str = typer.Option("openapi.yml", "-o", help="Output file path")):
    """Generate OpenAPI specification."""
    from tracecat.api.app import app

    openapi = app.openapi()
    if (version := openapi.get("openapi")) is None:
        rich.print("[yellow]Could not determine the OpenAPI version.[/yellow]")
    version = f"v{version}" if version else "unknown version"
    rich.print(f"Writing openapi spec {version}")

    with open(outfile, "w") as f:
        if outfile.endswith(".json"):
            json.dump(openapi, f, indent=2)
        else:
            yaml.dump(openapi, f, sort_keys=False)

    rich.print(f"Spec written to {outfile}")
