from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import orjson
import rich
import typer
import yaml
from pydantic import BaseModel

from ._config import config
from ._utils import read_input, user_client

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

    payload = read_input(data) if data else None
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


class Page(BaseModel):
    group: str
    pages: list[str | Page]


def get_ns_tree(keys: list[str]):
    root = {}
    for key in sorted(keys):
        parts = key.split(".")
        current = root
        for part in parts:
            current = current.setdefault(part, {})
    return root


def convert_ns_tree_to_pages(root: dict, *, path: list[str]) -> Page | str:
    if not root:
        base, *rest = path
        return f"{base}/{"_".join(rest)}"
    pages = []
    for key, value in root.items():
        pages.append(convert_ns_tree_to_pages(value, path=path + [key]))
    return Page(group=path[-1], pages=pages)


def key_tree_to_pages(keys: list[str], base: str) -> Page:
    ktree = get_ns_tree(keys)
    pages = convert_ns_tree_to_pages(ktree, path=[base])
    return pages


UDF_MDX_TEMPLATE = """---
title: {udf_name}
description: {udf_key}
---

{udf_desc}

This is the [JSONSchema7](https://json-schema.org/draft-07/json-schema-release-notes) definition for the `{udf_key}` integration.


## Secrets
{required_secrets}

## Inputs

<CodeGroup>
```json JSONSchema7 Definition
{input_schema}
```

</CodeGroup>

## Response

<CodeGroup>
```json JSONSchema7 Definition
{response_schema}
```

</CodeGroup>
"""


def create_markdown_table(data: dict[str, Any]) -> str:
    """Create a markdown table from a dictionary."""
    header = ["| Name | Keys |", "| --- | --- |"]
    body = []
    for key, value in data.items():
        body.append(f"| {key} | {value} |")
    return "\n".join(header + body)


@app.command(name="generate-udf-docs", help="Generate UDF documentation.")
def generate_udf_docs():
    """Generate UDF docs.

    Usage
    -----
    Run this from the Tracecat root directory:
    >>> tracecat dev generate-udf-docs
    """

    int_relpath = "integrations/udfs"
    path = config.docs_path / int_relpath

    # Empty out the directory
    try:
        shutil.rmtree(path)
    except FileNotFoundError:
        pass
    path.mkdir(parents=True, exist_ok=True)

    from tracecat.registry import registry

    registry.init()

    rich.print(f"Generating API reference paths in {config.docs_path!s}")

    for key, udf in registry:
        if udf.metadata.get("include_in_schema") is False:
            continue

        schema = udf.construct_schema()
        required_secrets = (
            create_markdown_table(
                {
                    f"`{secret.name}`": ", ".join(f"`{k}`" for k in secret.keys)
                    for secret in udf.secrets
                }
            )
            if udf.secrets
            else "_No secrets required._"
        )
        s = UDF_MDX_TEMPLATE.format(
            # Default title or the last part of the key
            udf_name=udf.metadata.get("default_title")
            or udf.key.split(".")[-1].title(),
            udf_key=key,
            # Add a period at the end if it doesn't have one
            udf_desc=udf.description
            if udf.description.endswith(".")
            else udf.description + ".",
            # Required secrets
            required_secrets=required_secrets,
            input_schema=json.dumps(schema["args"], indent=4, sort_keys=True),
            response_schema=json.dumps(schema["rtype"], indent=4, sort_keys=True),
        )

        with path.joinpath(f"{udf.key.replace(".","_")}.mdx").open("w") as f:
            f.write(s)

    mint_cfg = config.docs_path / "mint.json"
    with mint_cfg.open() as file:
        mint_data = json.load(file)
    # Overwrite the 'navigation' property with the new JSON data
    gname = "Schemas"
    # Find 'Schemas' group
    filtered_keys = [
        key
        for key, udf in registry
        if udf.metadata.get("include_in_schema") is not False
    ]
    new_mint_pages = key_tree_to_pages(filtered_keys, int_relpath).model_dump()["pages"]
    try:
        schemas_ref = next(
            item for item in mint_data["navigation"] if item["group"] == gname
        )
        schemas_ref["pages"] = new_mint_pages
    except StopIteration:
        # No Reference group found, create it
        rich.print(f"No {gname!r} group found in mint.json, creating...")
        schemas_ref["pages"].append({"group": "Schemas", "pages": new_mint_pages})

    # Save the updated JSON back to 'mint.json'
    with mint_cfg.open("w") as file:
        json.dump(mint_data, file, indent=2)

    rich.print("UDF docs updated!")
