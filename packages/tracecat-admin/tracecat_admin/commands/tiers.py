"""Tier management commands."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from functools import wraps
from typing import Annotated, Any

import typer

from tracecat_admin.client import AdminClient, AdminClientError
from tracecat_admin.output import (
    print_error,
    print_org_tier_detail,
    print_success,
    print_tier_detail,
    print_tiers_table,
)

app = typer.Typer(no_args_is_help=True)


def async_command[F: Callable[..., Any]](func: F) -> F:
    """Decorator to run async functions in typer commands."""

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return asyncio.run(func(*args, **kwargs))

    return wrapper  # type: ignore[return-value]


def _parse_entitlements(values: list[str] | None) -> dict[str, bool] | None:
    """Parse entitlement key=value pairs into a dict.

    Args:
        values: List of "key=value" strings (e.g., ["custom_registry=true", "sso=false"])

    Returns:
        Dict of entitlements or None if no values provided
    """
    if not values:
        return None
    result: dict[str, bool] = {}
    for item in values:
        if "=" not in item:
            raise typer.BadParameter(
                f"Invalid entitlement format: '{item}'. Expected KEY=VALUE (e.g., custom_registry=true)"
            )
        key, value = item.split("=", 1)
        key = key.strip()
        value = value.strip().lower()
        if value in ("true", "1", "yes"):
            result[key] = True
        elif value in ("false", "0", "no"):
            result[key] = False
        else:
            raise typer.BadParameter(
                f"Invalid entitlement value for '{key}': '{value}'. Expected true/false"
            )
    return result


@app.command("list")
@async_command
async def list_tiers(
    include_inactive: Annotated[
        bool,
        typer.Option("--include-inactive", "-a", help="Include inactive tiers"),
    ] = False,
    json_output: Annotated[
        bool, typer.Option("--json", "-j", help="Output as JSON")
    ] = False,
) -> None:
    """List all tiers."""
    try:
        async with AdminClient() as client:
            tiers = await client.list_tiers(include_inactive=include_inactive)

        if json_output:
            typer.echo(json.dumps([t.model_dump(mode="json") for t in tiers], indent=2))
        else:
            if not tiers:
                typer.echo("No tiers found.")
            else:
                print_tiers_table(tiers)
    except AdminClientError as e:
        print_error(str(e))
        raise typer.Exit(1) from None


@app.command("get")
@async_command
async def get_tier(
    tier_id: Annotated[str, typer.Argument(help="Tier ID")],
    json_output: Annotated[
        bool, typer.Option("--json", "-j", help="Output as JSON")
    ] = False,
) -> None:
    """Get details for a specific tier."""
    try:
        async with AdminClient() as client:
            tier = await client.get_tier(tier_id)

        if json_output:
            typer.echo(json.dumps(tier.model_dump(mode="json"), indent=2))
        else:
            print_tier_detail(tier)
    except AdminClientError as e:
        print_error(str(e))
        raise typer.Exit(1) from None


@app.command("create")
@async_command
async def create_tier(
    display_name: Annotated[
        str,
        typer.Option("--display-name", "-n", help="Display name for the tier"),
    ],
    max_concurrent_workflows: Annotated[
        int | None,
        typer.Option("--max-concurrent-workflows", help="Max concurrent workflows"),
    ] = None,
    max_action_executions_per_workflow: Annotated[
        int | None,
        typer.Option(
            "--max-action-executions-per-workflow",
            help="Max action executions per workflow",
        ),
    ] = None,
    max_concurrent_actions: Annotated[
        int | None,
        typer.Option("--max-concurrent-actions", help="Max concurrent actions"),
    ] = None,
    api_rate_limit: Annotated[
        int | None,
        typer.Option("--api-rate-limit", help="API rate limit (requests per second)"),
    ] = None,
    api_burst_capacity: Annotated[
        int | None,
        typer.Option("--api-burst-capacity", help="API burst capacity"),
    ] = None,
    entitlement: Annotated[
        list[str] | None,
        typer.Option(
            "--entitlement",
            "-e",
            help="Entitlement KEY=VALUE (repeatable, e.g., --entitlement custom_registry=true)",
        ),
    ] = None,
    is_default: Annotated[
        bool,
        typer.Option("--default/--no-default", help="Set as default tier"),
    ] = False,
    sort_order: Annotated[
        int,
        typer.Option("--sort-order", help="Sort order for display"),
    ] = 0,
    json_output: Annotated[
        bool, typer.Option("--json", "-j", help="Output as JSON")
    ] = False,
) -> None:
    """Create a new tier."""
    try:
        entitlements = _parse_entitlements(entitlement)

        async with AdminClient() as client:
            tier = await client.create_tier(
                display_name=display_name,
                max_concurrent_workflows=max_concurrent_workflows,
                max_action_executions_per_workflow=max_action_executions_per_workflow,
                max_concurrent_actions=max_concurrent_actions,
                api_rate_limit=api_rate_limit,
                api_burst_capacity=api_burst_capacity,
                entitlements=entitlements,
                is_default=is_default,
                sort_order=sort_order,
            )

        if json_output:
            typer.echo(json.dumps(tier.model_dump(mode="json"), indent=2))
        else:
            print_success(
                f"Tier '{tier.display_name}' created successfully (ID: {tier.id})"
            )
            print_tier_detail(tier)
    except AdminClientError as e:
        print_error(str(e))
        raise typer.Exit(1) from None


@app.command("update")
@async_command
async def update_tier(
    tier_id: Annotated[str, typer.Argument(help="Tier ID to update")],
    display_name: Annotated[
        str | None,
        typer.Option("--display-name", "-n", help="New display name"),
    ] = None,
    max_concurrent_workflows: Annotated[
        int | None,
        typer.Option("--max-concurrent-workflows", help="Max concurrent workflows"),
    ] = None,
    max_action_executions_per_workflow: Annotated[
        int | None,
        typer.Option(
            "--max-action-executions-per-workflow",
            help="Max action executions per workflow",
        ),
    ] = None,
    max_concurrent_actions: Annotated[
        int | None,
        typer.Option("--max-concurrent-actions", help="Max concurrent actions"),
    ] = None,
    api_rate_limit: Annotated[
        int | None,
        typer.Option("--api-rate-limit", help="API rate limit (requests per second)"),
    ] = None,
    api_burst_capacity: Annotated[
        int | None,
        typer.Option("--api-burst-capacity", help="API burst capacity"),
    ] = None,
    entitlement: Annotated[
        list[str] | None,
        typer.Option(
            "--entitlement",
            "-e",
            help="Entitlement KEY=VALUE (repeatable)",
        ),
    ] = None,
    is_default: Annotated[
        bool | None,
        typer.Option("--default/--no-default", help="Set as default tier"),
    ] = None,
    sort_order: Annotated[
        int | None,
        typer.Option("--sort-order", help="Sort order for display"),
    ] = None,
    active: Annotated[
        bool | None,
        typer.Option("--active/--inactive", help="Set tier active status"),
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", "-j", help="Output as JSON")
    ] = False,
) -> None:
    """Update a tier."""
    # Check if at least one update option is provided
    has_update = any(
        x is not None
        for x in [
            display_name,
            max_concurrent_workflows,
            max_action_executions_per_workflow,
            max_concurrent_actions,
            api_rate_limit,
            api_burst_capacity,
            entitlement,
            is_default,
            sort_order,
            active,
        ]
    )
    if not has_update:
        print_error("At least one option is required to update the tier")
        raise typer.Exit(1)

    try:
        entitlements = _parse_entitlements(entitlement)

        async with AdminClient() as client:
            tier = await client.update_tier(
                tier_id=tier_id,
                display_name=display_name,
                max_concurrent_workflows=max_concurrent_workflows,
                max_action_executions_per_workflow=max_action_executions_per_workflow,
                max_concurrent_actions=max_concurrent_actions,
                api_rate_limit=api_rate_limit,
                api_burst_capacity=api_burst_capacity,
                entitlements=entitlements,
                is_default=is_default,
                sort_order=sort_order,
                is_active=active,
            )

        if json_output:
            typer.echo(json.dumps(tier.model_dump(mode="json"), indent=2))
        else:
            print_success(f"Tier '{tier_id}' updated successfully")
            print_tier_detail(tier)
    except AdminClientError as e:
        print_error(str(e))
        raise typer.Exit(1) from None


@app.command("delete")
@async_command
async def delete_tier(
    tier_id: Annotated[str, typer.Argument(help="Tier ID to delete")],
    force: Annotated[
        bool,
        typer.Option("--force", "-f", help="Skip confirmation prompt"),
    ] = False,
) -> None:
    """Delete a tier (only if no orgs are assigned to it)."""
    try:
        async with AdminClient() as client:
            # Get tier details for confirmation
            tier = await client.get_tier(tier_id)

            if not force:
                confirm = typer.confirm(
                    f"Are you sure you want to delete tier '{tier.display_name}' ({tier_id})?"
                )
                if not confirm:
                    typer.echo("Aborted.")
                    raise typer.Exit(0)

            await client.delete_tier(tier_id)
            print_success(f"Tier '{tier_id}' deleted successfully")
    except AdminClientError as e:
        print_error(str(e))
        raise typer.Exit(1) from None


# Organization tier subcommand group
org_app = typer.Typer(no_args_is_help=True)
app.add_typer(org_app, name="org", help="Organization tier management.")


def _select_from_list(prompt: str, items: list[tuple[str, str]]) -> str | None:
    """Interactive selection using numbered list.

    Args:
        prompt: The prompt to display
        items: List of (display_text, value) tuples

    Returns:
        Selected value or None if cancelled
    """
    from rich.console import Console
    from rich.table import Table

    console = Console()

    table = Table(show_header=True, header_style="bold")
    table.add_column("#", style="dim", width=4)
    table.add_column("Option")

    for i, (display, _) in enumerate(items, 1):
        table.add_row(str(i), display)

    console.print(table)

    while True:
        try:
            choice = typer.prompt(prompt, default="1")
            if choice.lower() in ("q", "quit", "exit"):
                return None
            idx = int(choice) - 1
            if 0 <= idx < len(items):
                return items[idx][1]
            typer.echo(f"Please enter a number between 1 and {len(items)}")
        except ValueError:
            typer.echo("Please enter a valid number (or 'q' to quit)")


@org_app.command("get")
@async_command
async def get_org_tier(
    org_id: Annotated[str | None, typer.Argument(help="Organization ID (UUID)")] = None,
    json_output: Annotated[
        bool, typer.Option("--json", "-j", help="Output as JSON")
    ] = False,
) -> None:
    """Get tier assignment for an organization.

    If org_id is not provided, shows interactive selection.
    """
    try:
        async with AdminClient() as client:
            # Interactive org selection if not provided
            if org_id is None:
                orgs = await client.list_organizations()
                if not orgs:
                    print_error("No organizations found.")
                    raise typer.Exit(1)

                items = [(f"{org.name} ({org.slug})", str(org.id)) for org in orgs]
                org_id = _select_from_list("Select organization number", items)
                if org_id is None:
                    typer.echo("Operation cancelled.")
                    raise typer.Exit(0)

            org_tier = await client.get_org_tier(org_id)

        if json_output:
            typer.echo(json.dumps(org_tier.model_dump(mode="json"), indent=2))
        else:
            print_org_tier_detail(org_tier)
    except AdminClientError as e:
        print_error(str(e))
        raise typer.Exit(1) from None


@org_app.command("update")
@async_command
async def update_org_tier(
    org_id: Annotated[str | None, typer.Argument(help="Organization ID (UUID)")] = None,
    tier_id: Annotated[
        str | None,
        typer.Option("--tier-id", "-t", help="Tier ID to assign"),
    ] = None,
    max_concurrent_workflows: Annotated[
        int | None,
        typer.Option(
            "--max-concurrent-workflows", help="Override max concurrent workflows"
        ),
    ] = None,
    max_action_executions_per_workflow: Annotated[
        int | None,
        typer.Option(
            "--max-action-executions-per-workflow",
            help="Override max action executions per workflow",
        ),
    ] = None,
    max_concurrent_actions: Annotated[
        int | None,
        typer.Option(
            "--max-concurrent-actions", help="Override max concurrent actions"
        ),
    ] = None,
    api_rate_limit: Annotated[
        int | None,
        typer.Option("--api-rate-limit", help="Override API rate limit"),
    ] = None,
    api_burst_capacity: Annotated[
        int | None,
        typer.Option("--api-burst-capacity", help="Override API burst capacity"),
    ] = None,
    entitlement_override: Annotated[
        list[str] | None,
        typer.Option(
            "--entitlement-override",
            "-e",
            help="Entitlement override KEY=VALUE (repeatable)",
        ),
    ] = None,
    clear_overrides: Annotated[
        bool,
        typer.Option(
            "--clear-overrides", help="Clear all overrides (use tier defaults)"
        ),
    ] = False,
    json_output: Annotated[
        bool, typer.Option("--json", "-j", help="Output as JSON")
    ] = False,
) -> None:
    """Update organization's tier assignment and overrides.

    If org_id is not provided, shows interactive selection.
    """
    # Check if at least one update option is provided (except clear_overrides which is standalone)
    has_update = (
        any(
            x is not None
            for x in [
                tier_id,
                max_concurrent_workflows,
                max_action_executions_per_workflow,
                max_concurrent_actions,
                api_rate_limit,
                api_burst_capacity,
                entitlement_override,
            ]
        )
        or clear_overrides
    )

    if not has_update:
        print_error(
            "At least one of --tier-id, override options, or --clear-overrides is required"
        )
        raise typer.Exit(1)

    try:
        entitlement_overrides = _parse_entitlements(entitlement_override)

        async with AdminClient() as client:
            # Interactive org selection if not provided
            if org_id is None:
                orgs = await client.list_organizations()
                if not orgs:
                    print_error("No organizations found.")
                    raise typer.Exit(1)

                items = [(f"{org.name} ({org.slug})", str(org.id)) for org in orgs]
                org_id = _select_from_list("Select organization number", items)
                if org_id is None:
                    typer.echo("Operation cancelled.")
                    raise typer.Exit(0)

            org_tier = await client.update_org_tier(
                org_id=org_id,
                tier_id=tier_id,
                max_concurrent_workflows=max_concurrent_workflows,
                max_action_executions_per_workflow=max_action_executions_per_workflow,
                max_concurrent_actions=max_concurrent_actions,
                api_rate_limit=api_rate_limit,
                api_burst_capacity=api_burst_capacity,
                entitlement_overrides=entitlement_overrides,
                clear_overrides=clear_overrides,
            )

        if json_output:
            typer.echo(json.dumps(org_tier.model_dump(mode="json"), indent=2))
        else:
            print_success("Organization tier updated successfully")
            print_org_tier_detail(org_tier)
    except AdminClientError as e:
        print_error(str(e))
        raise typer.Exit(1) from None
