from __future__ import annotations

import io
import json
import shutil
import subprocess
import tempfile
from collections import defaultdict
from itertools import chain
from pathlib import Path
from typing import Any

import httpx
import orjson
import rich
import typer
import yaml
from pydantic import BaseModel

from .client import Client
from .config import config
from .utils import read_input

app = typer.Typer(no_args_is_help=True, help="Dev tools.")


def hit_api_endpoint(
    method: str, endpoint: str, payload: dict[str, str] | None
) -> dict[str, Any]:
    with Client() as client:
        content = orjson.dumps(payload) if payload else None
        res = client.request(method=method, url=endpoint, content=content)
        res.raise_for_status()
    return res.json()


@app.command(name="validate", help="Validate a workflow definition.")
def validate_workflow(
    file: Path = typer.Option(
        None, "--file", "-f", help="Path to the workflow definition YAML file"
    ),
    all: bool = typer.Option(False, "--all", "-a", help="Validate all yaml files"),
    data: str = typer.Option(
        None, "--data", "-d", help="Pass a JSON Payload to be validated"
    ),
):
    """Validate a workflow definition."""
    if not file and not all:
        return rich.print("[red]Must specify either --file or --all[/red]")

    if data and not file:
        return rich.print(
            "[red]Payload provided but no file specified - please provide a workflow definition yaml.[/red]"
        )

    payload = read_input(data) if data else None

    def validate_file(file: Path):
        with file.open() as f:
            yaml_content = f.read()
        files = {}
        files["definition"] = (file.name, yaml_content, "application/yaml")
        if payload:
            payload_bytes = io.BytesIO(orjson.dumps(payload))
            files["payload"] = ("payload", payload_bytes, "application/json")
        rich.print(f"Validating {file.name}")
        try:
            with Client() as client:
                res = client.post("/validate-workflow", files=files)
                result = Client.handle_response(res)
                rich.print(result)
        except httpx.HTTPStatusError as e:
            rich.print("[red]Failed to validate workflow![/red]")
            rich.print(
                orjson.dumps(e.response.json(), option=orjson.OPT_INDENT_2).decode()
            )

    if all:
        playbooks_path = Path.cwd().joinpath("playbooks")
        playbooks = list(playbooks_path.glob("**/*.yml"))

        # unit_tests_path = Path.cwd().joinpath("tests/data/workflows")
        # test_yamls = list(unit_tests_path.glob("**/*.yml"))
        files = playbooks  # + test_yamls
    else:
        files = [file]

    for file in files:
        validate_file(file)


@app.command(help="Hit the API endpoint with an authenticated service client.")
def api(
    endpoint: str = typer.Argument(..., help="Endpoint to hit"),
    method: str = typer.Option("GET", "-X", help="HTTP Method"),
    data: str = typer.Option(None, "--data", "-d", help="JSON Payload to send"),
):
    """Commit a workflow definition to the database."""

    payload = read_input(data) if data else None
    result = hit_api_endpoint(method, endpoint, payload)
    rich.print("Hit the endpoint successfully!")
    rich.print(result, len(result))


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
    use_npx: bool = typer.Option(
        False,
        "--npx",
        help="Use npx to run the Mintlify scraping package.",
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

    # NOTE: If this hangs, likely the mintlify scraping package is trying to update (reading stdin)
    # Define the command that generates the output
    rich.print(
        "[yellow]Running Mintlify scraping package... "
        "(if this hangs, you likely need to update 'mintlify-scrape'. "
        "To fix, `cd docs && npx @mintlify/scraping@latest`)[/yellow]"
    )
    if use_npx:
        executable = "npx @mintlify/scraping@latest"
    else:
        executable = "mintlify-scrape"

    cmd = (
        f"cd {config.docs_path!s} &&"
        f" {executable}"
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


def pad(text: str, *, n: int = 1) -> str:
    return wrap(text, " ", n=n)


def wrap(text: str, wrp: str, *, n: int = 1) -> str:
    return wrp * n + text + wrp * n


def create_markdown_table(header: tuple[str, ...], rows: list[tuple[Any, ...]]) -> str:
    """Create a markdown table from a list of tuples."""
    n_cols = len(header)
    if n_cols != len(rows[0]):
        raise ValueError("Number of columns in header and rows do not match.")
    header = [
        wrap("|".join(pad(h) for h in header), "|"),  # Header
        wrap("|".join(" --- " for _ in range(n_cols)), "|"),  # Separator
    ]
    body = []
    for row in rows:
        body.append(wrap("|".join(pad(str(v)) for v in row), "|"))
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
        if not udf.metadata["include_in_schema"]:
            continue

        schema = udf.construct_schema()
        required_secrets = (
            create_markdown_table(
                header=("Name", "Keys"),
                rows=[
                    (
                        wrap(secret.name, "`"),
                        ", ".join(wrap(k, "`") for k in secret.keys),
                    )
                    for secret in udf.secrets
                ],
            )
            if udf.secrets
            else "_No secrets required._"
        )
        s = UDF_MDX_TEMPLATE.format(
            # Default title or the last part of the key
            udf_name=udf.metadata["default_title"] or udf.key.split(".")[-1].title(),
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
    filtered_keys = [key for key in registry.keys if udf.metadata["include_in_schema"]]
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


SECRETS_CHEATSHEET = """---
title: Secrets Cheatsheet
description: A cheatsheet of all the secrets required by the UDFs and integrations.
---
## API Credentials
The secret keys required by each secret are listed below.
{api_credentials_table}

## Core Actions
Note that the fully qualified namespace for each Core Action UDF is prefixed with `core.`.
{core_udfs_secrets_table}

## Integrations
Note that the fully qualified namespace for each Integration UDF is prefixed with `integrations.`.
{integrations_secrets_table}

## ETL Actions
Note that the fully qualified namespace for each ETL UDF is prefixed with `etl.`.
{etl_secrets_table}
"""


@app.command(name="generate-secrets", help="Generate secrets.")
def generate_secret_tables(
    file: Path = typer.Option(
        config.docs_path / "integrations/secrets_cheatsheet.mdx",
        "-o",
        help="Output file path",
    ),
):
    from tracecat.registry import registry

    registry.init()

    # Table of core UDFs required secrets
    secrets = defaultdict(list)
    # Get UDF -> Secrets mapping
    blacklist = {"example"}
    for key, udf in registry:
        top_level_ns, *stem, func = key.split(".")
        if top_level_ns in blacklist:
            continue
        secrets[top_level_ns].append(
            (
                ".".join(stem) if stem else "-",
                func,
                ", ".join(wrap(s.name, "`") for s in udf.secrets)
                if udf.secrets
                else "-",
            )
        )

    # Get all secrets -> secret keys
    api_credentials = set()
    for secret in chain.from_iterable(udf.secrets or [] for _, udf in registry):
        api_credentials.add(
            (
                wrap(secret.name, "`"),
                ", ".join(wrap(k, "`") for k in sorted(secret.keys)),
            )
        )

    page_content = SECRETS_CHEATSHEET.format(
        api_credentials_table=create_markdown_table(
            header=("Secret Name", "Secret Keys"), rows=list(api_credentials)
        ),
        core_udfs_secrets_table=create_markdown_table(
            header=("Sub-namespace", "Function", "Secrets"), rows=secrets["core"]
        ),
        integrations_secrets_table=create_markdown_table(
            header=("Sub-namespace", "Function", "Secrets"),
            rows=secrets["integrations"],
        ),
        etl_secrets_table=create_markdown_table(
            header=("Sub-namespace", "Function", "Secrets"), rows=secrets["etl"]
        ),
    )

    if file.exists():
        file.unlink(missing_ok=True)
    with file.open("w") as f:
        f.write(page_content)


@app.command(name="delete-test-resources", help="Generate UDF documentation.")
def delete_test_resources(
    delete_workflow: bool = typer.Option(
        False, "--workflow", help="Delete all test workflows."
    ),
    delete_secrets: bool = typer.Option(
        False, "--secrets", help="Delete all test secrets."
    ),
    prefix: str = typer.Option(
        "__test", "--prefix", help="Prefix of the test resources."
    ),
):
    if delete_workflow:
        cmd = f"tracecat workflow list --json | jq '[.[] | select(.title | startswith(\"{prefix}\"))]'"
        res = subprocess.run(cmd, shell=True, text=True, capture_output=True)
        if res.returncode != 0:
            return rich.print("[red]Failed to list test resources.[/red]")

        workflows = orjson.loads(res.stdout)
        rich.print(workflows)
        if not res.stdout:
            return rich.print("No test resources found.")

        to_delete = [
            f"{workflow['title']} ({workflow['id']})" for workflow in workflows
        ]
        if not typer.confirm(
            f"Are you sure you want to delete workflows: {to_delete}?"
        ):
            return rich.print("Aborted.")
        rich.print(f"Deleting {len(workflows)} test workflows...")
        all_ids = " ".join(workflow["id"] for workflow in workflows)
        cmd = f"echo 'y' | tracecat workflow delete {all_ids} > /dev/null"
        delete_res = subprocess.run(cmd, shell=True, text=True)
        if delete_res.returncode != 0:
            return rich.print(
                "[red]Failed to delete test workflows.[/red]", delete_res.stderr
            )

    if delete_secrets:
        cmd = f"tracecat secret list --json | jq '[.[] | select(.name | startswith(\"{prefix}\"))]'"
        res = subprocess.run(cmd, shell=True, text=True, capture_output=True)
        if res.returncode != 0:
            return rich.print("[red]Failed to list test resources.[/red]")

        secrets = orjson.loads(res.stdout)
        if not secrets:
            return rich.print("No test resources found.")

        to_delete = [f"{secret['name']} ({secret['id']})" for secret in secrets]
        if not typer.confirm(f"Are you sure you want to delete secrets: {to_delete}?"):
            return rich.print("Aborted.")

        rich.print(f"Deleting {len(secrets)} test secrets...")
        all_ids = " ".join(secret["id"] for secret in secrets)
        cmd = f"echo 'y' | tracecat secret delete {all_ids} > /dev/null"
        delete_res = subprocess.run(cmd, shell=True, text=True)
        if delete_res.returncode != 0:
            return rich.print(
                "[red]Failed to delete test secrets.[/red]", delete_res.stderr
            )
