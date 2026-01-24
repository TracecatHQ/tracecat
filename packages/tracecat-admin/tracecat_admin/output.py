"""Output formatting utilities for CLI."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from rich.console import Console
from rich.table import Table

from tracecat_admin.schemas import (
    OrganizationTierRead,
    OrgRead,
    OrgRegistryRepositoryRead,
    OrgRegistrySyncResponse,
    OrgRegistryVersionPromoteResponse,
    RegistryStatusResponse,
    RegistrySyncResponse,
    RegistryVersionPromoteResponse,
    RegistryVersionRead,
    TierRead,
    UserRead,
)

console = Console()
error_console = Console(stderr=True)


def format_datetime(dt: str | datetime | None) -> str:
    """Format a datetime string for display."""
    if dt is None:
        return "-"
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        except ValueError:
            return dt
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def print_json(data: Any) -> None:
    """Print data as formatted JSON."""
    console.print_json(json.dumps(data, default=str, indent=2))


def print_success(message: str) -> None:
    """Print a success message."""
    console.print(f"[green]{message}[/green]")


def print_error(message: str) -> None:
    """Print an error message to stderr."""
    error_console.print(f"[red]Error:[/red] {message}")


def print_warning(message: str) -> None:
    """Print a warning message."""
    console.print(f"[yellow]Warning:[/yellow] {message}")


def print_users_table(users: list[UserRead]) -> None:
    """Print users in a table format."""
    table = Table(title="Users")
    table.add_column("ID", style="dim")
    table.add_column("Email", style="cyan")
    table.add_column("Name")
    table.add_column("Role", style="magenta")
    table.add_column("Superuser", style="green")
    table.add_column("Active", style="blue")
    table.add_column("Last Login")

    for user in users:
        name = " ".join(filter(None, [user.first_name, user.last_name])) or "-"
        table.add_row(
            str(user.id),
            user.email,
            name,
            user.role,
            "Yes" if user.is_superuser else "No",
            "Yes" if user.is_active else "No",
            format_datetime(user.last_login_at),
        )

    console.print(table)


def print_user_detail(user: UserRead) -> None:
    """Print user details."""
    table = Table(title="User Details", show_header=False)
    table.add_column("Field", style="dim")
    table.add_column("Value")

    name = " ".join(filter(None, [user.first_name, user.last_name])) or "-"

    table.add_row("ID", str(user.id))
    table.add_row("Email", user.email)
    table.add_row("Name", name)
    table.add_row("Role", user.role)
    table.add_row("Superuser", "Yes" if user.is_superuser else "No")
    table.add_row("Active", "Yes" if user.is_active else "No")
    table.add_row("Verified", "Yes" if user.is_verified else "No")
    table.add_row("Last Login", format_datetime(user.last_login_at))
    table.add_row("Created", format_datetime(user.created_at))

    console.print(table)


def print_orgs_table(orgs: list[OrgRead]) -> None:
    """Print organizations in a table format."""
    table = Table(title="Organizations")
    table.add_column("ID", style="dim")
    table.add_column("Name", style="cyan")
    table.add_column("Slug", style="magenta")
    table.add_column("Active", style="green")
    table.add_column("Created")

    for org in orgs:
        table.add_row(
            str(org.id),
            org.name,
            org.slug,
            "Yes" if org.is_active else "No",
            format_datetime(org.created_at),
        )

    console.print(table)


def print_org_detail(org: OrgRead) -> None:
    """Print organization details."""
    table = Table(title="Organization Details", show_header=False)
    table.add_column("Field", style="dim")
    table.add_column("Value")

    table.add_row("ID", str(org.id))
    table.add_row("Name", org.name)
    table.add_row("Slug", org.slug)
    table.add_row("Active", "Yes" if org.is_active else "No")
    table.add_row("Created", format_datetime(org.created_at))
    table.add_row("Updated", format_datetime(org.updated_at))

    console.print(table)


def print_registry_status(status: RegistryStatusResponse) -> None:
    """Print registry status."""
    console.print("[bold]Registry Status[/bold]")
    console.print(f"  Total repositories: {status.total_repositories}")
    console.print(f"  Last sync: {format_datetime(status.last_sync_at)}")
    console.print()

    if status.repositories:
        table = Table(title="Repositories")
        table.add_column("ID", style="dim")
        table.add_column("Name", style="cyan")
        table.add_column("Origin", style="magenta")
        table.add_column("Commit SHA", style="dim")
        table.add_column("Last Synced")

        for repo in status.repositories:
            sha = repo.commit_sha or "-"
            if sha != "-":
                sha = sha[:8]  # Truncate SHA
            table.add_row(
                str(repo.id),
                repo.name,
                repo.origin,
                sha,
                format_datetime(repo.last_synced_at),
            )

        console.print(table)


def print_sync_result(result: RegistrySyncResponse) -> None:
    """Print registry sync result."""
    status = "[green]Success[/green]" if result.success else "[red]Failed[/red]"
    console.print(f"Sync status: {status}")
    console.print(f"Synced at: {format_datetime(result.synced_at)}")

    if result.repositories:
        console.print()
        table = Table(title="Sync Results")
        table.add_column("Repository", style="cyan")
        table.add_column("Status")
        table.add_column("Version", style="magenta")
        table.add_column("Actions", style="dim")
        table.add_column("Error")

        for repo in result.repositories:
            status_text = "[green]OK[/green]" if repo.success else "[red]Failed[/red]"
            table.add_row(
                repo.repository_name,
                status_text,
                repo.version or "-",
                str(repo.actions_count) if repo.actions_count is not None else "-",
                repo.error or "-",
            )

        console.print(table)


def print_registry_versions(versions: list[RegistryVersionRead]) -> None:
    """Print registry versions in a table format."""
    table = Table(title="Registry Versions")
    table.add_column("ID", style="dim")
    table.add_column("Repository ID", style="dim")
    table.add_column("Version", style="cyan")
    table.add_column("Commit SHA", style="magenta")
    table.add_column("Created")

    for version in versions:
        sha = version.commit_sha or "-"
        if sha != "-":
            sha = sha[:8]  # Truncate SHA
        table.add_row(
            str(version.id),
            str(version.repository_id),
            version.version,
            sha,
            format_datetime(version.created_at),
        )

    console.print(table)


def print_version_promote_result(result: RegistryVersionPromoteResponse) -> None:
    """Print the result of promoting a registry version."""
    console.print("[green]Version promoted successfully[/green]")
    console.print(f"  Repository: {result.origin}")
    if result.previous_version_id:
        console.print(f"  Previous version ID: {result.previous_version_id}")
    else:
        console.print("  Previous version: [dim]None[/dim]")
    console.print(f"  Current version: {result.version}")
    console.print(f"  Current version ID: {result.current_version_id}")


# Org Registry output functions


def print_org_registry_repositories_table(
    repos: list[OrgRegistryRepositoryRead],
) -> None:
    """Print organization registry repositories in a table format."""
    table = Table(title="Organization Registry Repositories")
    table.add_column("ID", style="dim")
    table.add_column("Origin", style="cyan")
    table.add_column("Commit SHA", style="magenta")
    table.add_column("Current Version ID", style="dim")
    table.add_column("Last Synced")

    for repo in repos:
        sha = repo.commit_sha or "-"
        if sha != "-":
            sha = sha[:8]  # Truncate SHA
        table.add_row(
            str(repo.id),
            repo.origin,
            sha,
            str(repo.current_version_id) if repo.current_version_id else "-",
            format_datetime(repo.last_synced_at),
        )

    console.print(table)


def print_org_registry_versions_table(versions: list[RegistryVersionRead]) -> None:
    """Print organization registry versions in a table format."""
    table = Table(title="Organization Registry Versions")
    table.add_column("ID", style="dim")
    table.add_column("Version", style="cyan")
    table.add_column("Commit SHA", style="magenta")
    table.add_column("Tarball URI", style="dim")
    table.add_column("Created")

    for version in versions:
        sha = version.commit_sha or "-"
        if sha != "-":
            sha = sha[:8]  # Truncate SHA
        tarball = version.tarball_uri or "-"
        if tarball != "-" and len(tarball) > 40:
            tarball = tarball[:37] + "..."
        table.add_row(
            str(version.id),
            version.version,
            sha,
            tarball,
            format_datetime(version.created_at),
        )

    console.print(table)


def print_org_registry_sync_result(result: OrgRegistrySyncResponse) -> None:
    """Print organization registry sync result."""
    if result.skipped:
        console.print("[yellow]Sync skipped[/yellow]")
        console.print(f"  Repository: {result.origin}")
        if result.version:
            console.print(f"  Current version: {result.version}")
        if result.message:
            console.print(f"  [dim]{result.message}[/dim]")
        return

    status = "[green]Success[/green]" if result.success else "[red]Failed[/red]"
    console.print(f"Sync status: {status}")
    console.print(f"  Repository: {result.origin}")
    console.print(f"  Repository ID: {result.repository_id}")
    if result.version:
        console.print(f"  Version: {result.version}")
    if result.commit_sha:
        console.print(f"  Commit SHA: {result.commit_sha[:8]}")
    if result.actions_count is not None:
        console.print(f"  Actions synced: {result.actions_count}")
    if result.forced:
        console.print("  [yellow]Force sync: previous version was deleted[/yellow]")


def print_org_version_promote_result(result: OrgRegistryVersionPromoteResponse) -> None:
    """Print the result of promoting an org registry version."""
    console.print("[green]Version promoted successfully[/green]")
    console.print(f"  Repository: {result.origin}")
    if result.previous_version:
        console.print(f"  Previous version: {result.previous_version}")
    else:
        console.print("  Previous version: [dim]None[/dim]")
    console.print(f"  Current version: {result.current_version}")


# Tier output functions


def _format_limit(value: int | None) -> str:
    """Format a limit value for display."""
    return str(value) if value is not None else "unlimited"


def _format_entitlements(entitlements: dict[str, bool]) -> str:
    """Format entitlements for display in a table cell."""
    enabled = [k for k, v in entitlements.items() if v]
    return ", ".join(enabled) if enabled else "-"


def print_tiers_table(tiers: list[TierRead]) -> None:
    """Print tiers in a table format."""
    table = Table(title="Tiers")
    table.add_column("ID", style="cyan")
    table.add_column("Display Name")
    table.add_column("Default", style="green")
    table.add_column("Active", style="blue")
    table.add_column("Concurrent Workflows")
    table.add_column("Actions/Workflow")
    table.add_column("Entitlements", style="magenta")

    for tier in tiers:
        table.add_row(
            tier.id,
            tier.display_name,
            "Yes" if tier.is_default else "No",
            "Yes" if tier.is_active else "No",
            _format_limit(tier.max_concurrent_workflows),
            _format_limit(tier.max_action_executions_per_workflow),
            _format_entitlements(tier.entitlements),
        )

    console.print(table)


def print_tier_detail(tier: TierRead) -> None:
    """Print single tier details."""
    table = Table(title="Tier Details", show_header=False)
    table.add_column("Field", style="dim")
    table.add_column("Value")

    table.add_row("ID", tier.id)
    table.add_row("Display Name", tier.display_name)
    table.add_row("Default", "Yes" if tier.is_default else "No")
    table.add_row("Active", "Yes" if tier.is_active else "No")
    table.add_row("Sort Order", str(tier.sort_order))
    table.add_row("", "")  # Spacer
    table.add_row("[bold]Resource Limits[/bold]", "")
    table.add_row(
        "Max Concurrent Workflows", _format_limit(tier.max_concurrent_workflows)
    )
    table.add_row(
        "Max Actions/Workflow", _format_limit(tier.max_action_executions_per_workflow)
    )
    table.add_row("Max Concurrent Actions", _format_limit(tier.max_concurrent_actions))
    table.add_row("API Rate Limit", _format_limit(tier.api_rate_limit))
    table.add_row("API Burst Capacity", _format_limit(tier.api_burst_capacity))
    table.add_row("", "")  # Spacer
    table.add_row("[bold]Entitlements[/bold]", "")
    for key, value in tier.entitlements.items():
        status = "[green]Yes[/green]" if value else "[red]No[/red]"
        table.add_row(f"  {key}", status)
    table.add_row("", "")  # Spacer
    table.add_row("Created", format_datetime(tier.created_at))
    table.add_row("Updated", format_datetime(tier.updated_at))

    console.print(table)


def print_org_tier_detail(org_tier: OrganizationTierRead) -> None:
    """Print organization tier assignment details."""
    table = Table(title="Organization Tier Assignment", show_header=False)
    table.add_column("Field", style="dim")
    table.add_column("Value")

    table.add_row("ID", str(org_tier.id))
    table.add_row("Organization ID", str(org_tier.organization_id))
    table.add_row("Tier ID", org_tier.tier_id)
    table.add_row("", "")  # Spacer

    # Show tier info if available
    if org_tier.tier:
        table.add_row("[bold]Tier Info[/bold]", "")
        table.add_row("  Tier Name", org_tier.tier.display_name)
        table.add_row("", "")

    table.add_row("[bold]Override Limits[/bold]", "(org-specific, overrides tier)")
    table.add_row(
        "Max Concurrent Workflows", _format_limit(org_tier.max_concurrent_workflows)
    )
    table.add_row(
        "Max Actions/Workflow",
        _format_limit(org_tier.max_action_executions_per_workflow),
    )
    table.add_row(
        "Max Concurrent Actions", _format_limit(org_tier.max_concurrent_actions)
    )
    table.add_row("API Rate Limit", _format_limit(org_tier.api_rate_limit))
    table.add_row("API Burst Capacity", _format_limit(org_tier.api_burst_capacity))

    table.add_row("", "")
    table.add_row("[bold]Entitlement Overrides[/bold]", "")
    if org_tier.entitlement_overrides:
        for key, value in org_tier.entitlement_overrides.items():
            status = "[green]Yes[/green]" if value else "[red]No[/red]"
            table.add_row(f"  {key}", status)
    else:
        table.add_row("  (none)", "-")

    table.add_row("", "")
    table.add_row("[bold]Subscription[/bold]", "")
    table.add_row("Stripe Customer ID", org_tier.stripe_customer_id or "-")
    table.add_row("Stripe Subscription ID", org_tier.stripe_subscription_id or "-")
    table.add_row("Expires At", format_datetime(org_tier.expires_at))

    table.add_row("", "")
    table.add_row("Created", format_datetime(org_tier.created_at))
    table.add_row("Updated", format_datetime(org_tier.updated_at))

    console.print(table)
