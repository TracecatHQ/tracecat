import asyncio
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import orjson
import rich
import typer
import yaml

from ._config import config
from ._utils import user_client

app = typer.Typer(no_args_is_help=True, help="Dev tools.")


async def hit_api_endpoint(
    method: str, endpoint: str, payload: dict[str, str] | None
) -> dict[str, Any]:
    async with user_client() as client:
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
    rich.print(config.role)


@app.command(name="generate-spec", help="Generate OpenAPI specification. Requires npx.")
def generate_spec(
    outfile: str = typer.Option(
        f"{config.docs_path!s}/openapi.yml", "-o", help="Output file path"
    ),
    update_docs: bool = typer.Option(
        False,
        "--update-docs",
        help="Update API reference paths in docs and update Mintlify config.",
    ),
):
    """Generate OpenAPI specification."""

    # Write the OpenAPI spec to docs/openapi.yml
    from tracecat.api.app import app

    openapi = app.openapi()
    if (version := openapi.get("openapi")) is None:
        rich.print("[yellow]Could not determine the OpenAPI version.[/yellow]")
    version = f"v{version}" if version else "unknown version"
    rich.print(f"Writing openapi spec {version}")

    outpath = Path(outfile)
    with outpath.open("w") as f:
        if outpath.suffix == ".json":
            json.dump(openapi, f, indent=2)
        else:
            yaml.dump(openapi, f, sort_keys=False)

    rich.print(f"Spec written to {outfile}")

    # Generate the API reference paths
    if not update_docs:
        return

    rich.print(f"Generating API reference paths in {config.docs_path!s}...")

    oas_relpath = outpath.relative_to(config.docs_path)

    # NOTE: If this hands, likely the mintlify scraping package is trying to update (reading stdin)
    # Define the command that generates the output
    cmd = (
        f"cd {config.docs_path!s} &&"
        "npx @mintlify/scraping@latest"
        f" openapi-file {oas_relpath!s}"  # This should be a relative path from within the docs root dir
        f" -o api-reference/reference"  # Output directory, relative to the docs root dir
    )

    # Create a temporary file to store the JSON output
    with tempfile.NamedTemporaryFile(mode="w+", delete=True) as tmpfile:
        # Run the command and capture the JSON output
        subprocess.run(
            f"{cmd} | awk '/navigation object suggestion:/ {{flag=1; next}} flag' > {tmpfile.name}",
            shell=True,
            check=True,
        )

        # Move the file cursor to the beginning of the temporary file
        tmpfile.seek(0)

        # Read the captured JSON data
        json_data = json.load(tmpfile)

    # Load the existing JSON from 'mint.json'
    mint_cfg = config.docs_path / "mint.json"
    with mint_cfg.open() as file:
        mint_data = json.load(file)

    # Overwrite the 'navigation' property with the new JSON data
    try:
        apidocs = next(
            item
            for item in mint_data["navigation"]
            if item["group"] == config.docs_api_group
        )
    except StopIteration as e:
        # Has no API Documentation group
        rich.print(f"[red]No {config.docs_api_group!r} group found in mint.json[/red]")
        raise typer.Exit() from e

    # NOTE: Customize this as how the API reference pages are grouped in the Mint config
    # Replace the object that has the 'group' property 'Reference'
    # and create it if it doesn't exist
    try:
        ref = next(
            item
            for item in apidocs["pages"]
            if isinstance(item, dict) and item["group"] == config.docs_api_pages_group
        )
        ref["pages"] = json_data
    except StopIteration:
        # No Reference group found, create it
        rich.print(
            f"[yellow]No {config.docs_api_pages_group!r} group found in mint.json, creating...[/yellow]"
        )
        apidocs["pages"].append(
            {"group": config.docs_api_pages_group, "pages": json_data}
        )

    # Add "/{openapi relative spec path}" to the navigation if it doesn't exist
    full_oas_relpath = f"/{oas_relpath!s}"
    if str(full_oas_relpath) not in mint_data["openapi"]:
        rich.print(f"Updating OpenAPI spec path: {full_oas_relpath!s}")
        mint_data["openapi"].append(str(full_oas_relpath))

    # Save the updated JSON back to 'mint.json'
    with mint_cfg.open("w") as file:
        json.dump(mint_data, file, indent=2)
    # Green
    rich.print(f"[green]API reference paths updated in {config.docs_path!s}[/green]")
