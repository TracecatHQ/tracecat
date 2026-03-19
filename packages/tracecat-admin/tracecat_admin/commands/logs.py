"""Local log tooling commands."""

from __future__ import annotations

from typing import Annotated

import typer

from tracecat_admin import config as admin_config
from tracecat_admin.logs import LogIdentifierType, compute_log_search_hash
from tracecat_admin.output import print_error, print_json

app = typer.Typer(no_args_is_help=True)


@app.command("hash")
def hash_identifier(
    value: Annotated[str, typer.Argument(help="Identifier value to hash locally")],
    identifier_type: Annotated[
        LogIdentifierType,
        typer.Option(
            "--type",
            "-t",
            case_sensitive=False,
            help="Identifier type to normalize and hash",
        ),
    ],
    json_output: Annotated[
        bool, typer.Option("--json", "-j", help="Output as JSON")
    ] = False,
) -> None:
    """Compute a local log-search hash without calling the Tracecat API."""
    try:
        result = compute_log_search_hash(
            identifier_type=identifier_type,
            value=value,
            key=admin_config.resolve_log_redaction_hmac_key(),
        )
    except ValueError as exc:
        print_error(str(exc))
        raise typer.Exit(1) from None

    if json_output:
        print_json(
            {
                "identifier_type": result.identifier_type.value,
                "field_name": result.field_name,
                "hash_value": result.hash_value,
            }
        )
        return

    typer.echo(f"{result.field_name}={result.hash_value}")
