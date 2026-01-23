"""Output formatting utilities for CLI."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from rich.console import Console
from rich.table import Table

from tracecat_admin.schemas import (
    OrgInviteResponse,
    OrgRead,
    OrgRegistryRepositoryRead,
    OrgRegistrySyncResponse,
    OrgRegistryVersionPromoteResponse,
    RegistryStatusResponse,
    RegistrySyncResponse,
    RegistryVersionPromoteResponse,
    RegistryVersionRead,
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


def print_invite_result(result: OrgInviteResponse) -> None:
    """Print the result of inviting a user to an organization."""
    if result.org_created:
        console.print(
            f'[green]Organization "{result.organization_name}" ({result.organization_slug}) created.[/green]'
        )

    console.print("[green]Invitation created.[/green]")
    console.print(f"  Email: {result.email}")
    console.print(f"  Role: {result.role}")
    console.print(
        f"  Organization: {result.organization_name} ({result.organization_slug})"
    )
    console.print()
    console.print(f"[bold]Magic link:[/bold] {result.magic_link}")
    console.print()

    if result.email_sent:
        console.print("[green]Email sent successfully.[/green]")
    else:
        console.print("[yellow]Email not sent.[/yellow]")
        if result.email_error:
            console.print(f"  [dim]Error: {result.email_error}[/dim]")
