"""Admin commands for user and superuser management."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from functools import wraps
from typing import Annotated, Any

import typer
from rich.text import Text

from tracecat_admin.client import AdminClient, AdminClientError
from tracecat_admin.output import (
    console,
    print_error,
    print_success,
    print_user_detail,
    print_users_table,
)

app = typer.Typer(no_args_is_help=True)


def async_command[F: Callable[..., Any]](func: F) -> F:
    """Decorator to run async functions in typer commands."""

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return asyncio.run(func(*args, **kwargs))

    return wrapper  # type: ignore[return-value]


def print_dev_seed(message: str) -> None:
    """Print a colorized dev seed message with a literal prefix."""
    text = Text("[dev-seed] ", style="green")
    text.append(message, style="green")
    console.print(text)


@app.command("list-users")
@async_command
async def list_users(
    json_output: Annotated[
        bool, typer.Option("--json", "-j", help="Output as JSON")
    ] = False,
) -> None:
    """List all users in the platform."""
    try:
        async with AdminClient() as client:
            users = await client.list_users()

        if json_output:
            import json

            typer.echo(json.dumps([u.model_dump(mode="json") for u in users], indent=2))
        else:
            if not users:
                typer.echo("No users found.")
            else:
                print_users_table(users)
    except AdminClientError as e:
        print_error(str(e))
        raise typer.Exit(1) from None


@app.command("get-user")
@async_command
async def get_user(
    user_id: Annotated[str, typer.Argument(help="User ID (UUID)")],
    json_output: Annotated[
        bool, typer.Option("--json", "-j", help="Output as JSON")
    ] = False,
) -> None:
    """Get details for a specific user."""
    try:
        async with AdminClient() as client:
            user = await client.get_user(user_id)

        if json_output:
            import json

            typer.echo(json.dumps(user.model_dump(mode="json"), indent=2))
        else:
            print_user_detail(user)
    except AdminClientError as e:
        print_error(str(e))
        raise typer.Exit(1) from None


@app.command("promote-user")
@async_command
async def promote_user(
    email: Annotated[str, typer.Option("--email", "-e", help="User email address")],
) -> None:
    """Promote a user to superuser status.

    Requires: TRACECAT__SERVICE_KEY environment variable.
    """
    try:
        async with AdminClient() as client:
            # First, find the user by email
            users = await client.list_users()
            user = next((u for u in users if u.email == email), None)

            if user is None:
                print_error(f"User with email '{email}' not found")
                raise typer.Exit(1)

            if user.is_superuser:
                print_error(f"User '{email}' is already a superuser")
                raise typer.Exit(1)

            # Promote the user
            updated_user = await client.promote_user(str(user.id))
            print_success(f"User '{updated_user.email}' promoted to superuser")
            print_user_detail(updated_user)
    except AdminClientError as e:
        print_error(str(e))
        raise typer.Exit(1) from None


@app.command("demote-user")
@async_command
async def demote_user(
    email: Annotated[str, typer.Option("--email", "-e", help="User email address")],
) -> None:
    """Remove superuser status from a user.

    Requires: TRACECAT__SERVICE_KEY environment variable.
    """
    try:
        async with AdminClient() as client:
            # First, find the user by email
            users = await client.list_users()
            user = next((u for u in users if u.email == email), None)

            if user is None:
                print_error(f"User with email '{email}' not found")
                raise typer.Exit(1)

            if not user.is_superuser:
                print_error(f"User '{email}' is not a superuser")
                raise typer.Exit(1)

            # Demote the user
            updated_user = await client.demote_user(str(user.id))
            print_success(f"User '{updated_user.email}' demoted from superuser")
            print_user_detail(updated_user)
    except AdminClientError as e:
        print_error(str(e))
        raise typer.Exit(1) from None


@app.command("create-superuser")
def create_superuser(
    email: Annotated[str, typer.Option("--email", "-e", help="User email address")],
    create: Annotated[
        bool,
        typer.Option(
            "--create",
            help="Create a new user if they don't exist (requires password prompt)",
        ),
    ] = False,
) -> None:
    """Create or promote a user to superuser status.

    This command operates directly on the database and is intended for bootstrap
    scenarios before any authenticated users exist.

    Default behavior: Promotes an existing user to superuser.
    With --create: Creates a new user with password, then promotes to superuser.

    Requires: TRACECAT__DB_URI environment variable or tracecat[bootstrap] installed.
    """
    try:
        from tracecat_admin.services.bootstrap import (
            create_superuser as bootstrap_create_superuser,
        )
    except ImportError:
        print_error(
            "Bootstrap dependencies not installed. "
            "Install with: pip install tracecat-admin[bootstrap]"
        )
        raise typer.Exit(1) from None

    if create:
        # Prompt for password
        password = typer.prompt("Password", hide_input=True)
        password_confirm = typer.prompt("Confirm password", hide_input=True)

        if password != password_confirm:
            print_error("Passwords do not match")
            raise typer.Exit(1)

        if len(password) < 12:
            print_error("Password must be at least 12 characters")
            raise typer.Exit(1)

    else:
        password = None

    try:
        result = asyncio.run(
            bootstrap_create_superuser(email=email, password=password, create=create)
        )
        if result.created:
            print_success(f"Created and promoted user '{email}' to superuser")
        else:
            print_success(f"Promoted existing user '{email}' to superuser")
    except Exception as e:
        print_error(str(e))
        raise typer.Exit(1) from None


@app.command("create-dev-user")
def create_dev_user(
    email: Annotated[
        str,
        typer.Option(
            "--email",
            "-e",
            help="Dev user email address",
            envvar="TRACECAT__DEV_USER_EMAIL",
        ),
    ] = "dev@tracecat.com",
    password: Annotated[
        str,
        typer.Option(
            "--password",
            "-p",
            help="Dev user password",
            envvar="TRACECAT__DEV_USER_PASSWORD",
        ),
    ] = "password1234",
    superuser_email: Annotated[
        str,
        typer.Option(
            "--superuser-email",
            help="Dev platform superuser email address",
            envvar="TRACECAT__DEV_SUPERUSER_EMAIL",
        ),
    ] = "test@tracecat.com",
    superuser_password: Annotated[
        str,
        typer.Option(
            "--superuser-password",
            help="Dev platform superuser password",
            envvar="TRACECAT__DEV_SUPERUSER_PASSWORD",
        ),
    ] = "password1234",
    default_tier_entitlements: Annotated[
        str,
        typer.Option(
            "--default-tier-entitlements",
            help="Default tier entitlements: all, none, or comma-separated names",
            envvar="TRACECAT__DEV_DEFAULT_TIER_ENTITLEMENTS",
        ),
    ] = "all",
    org_role: Annotated[
        str,
        typer.Option(
            "--org-role",
            help="Organization role slug to assign",
            envvar="TRACECAT__DEV_ORG_ROLE",
        ),
    ] = "organization-owner",
    workspace_role: Annotated[
        str,
        typer.Option(
            "--workspace-role",
            help="Workspace role slug to assign",
            envvar="TRACECAT__DEV_WORKSPACE_ROLE",
        ),
    ] = "workspace-admin",
) -> None:
    """Create local development superuser and tenant accounts.

    This command operates directly on the database and is intended for dev
    cluster seeding only. It creates or updates a platform superuser, creates
    or updates a tenant user, adds org and workspace memberships, and assigns
    RBAC roles.
    """
    try:
        from tracecat_admin.services.bootstrap import (
            create_dev_user as bootstrap_create_dev_user,
        )
    except ImportError:
        print_error(
            "Bootstrap dependencies not installed. "
            "Install with: pip install tracecat-admin[bootstrap]"
        )
        raise typer.Exit(1) from None

    if len(password) < 12:
        print_error("Password must be at least 12 characters")
        raise typer.Exit(1)
    if len(superuser_password) < 12:
        print_error("Superuser password must be at least 12 characters")
        raise typer.Exit(1)

    try:
        result = asyncio.run(
            bootstrap_create_dev_user(
                email=email,
                password=password,
                superuser_email=superuser_email,
                superuser_password=superuser_password,
                default_tier_entitlements=default_tier_entitlements,
                org_role=org_role,
                workspace_role=workspace_role,
            )
        )
        verb = "Created" if result.created else "Updated"
        superuser_verb = "Created" if result.superuser_created else "Updated"
        print_dev_seed(
            f"{superuser_verb} platform superuser '{result.superuser_email}'"
        )
        print_dev_seed(
            f"{verb} dev user '{result.email}' ({result.org_role}, {result.workspace_role})"
        )
        enabled_entitlements = [
            name
            for name, enabled in result.default_tier_entitlements.items()
            if enabled
        ]
        enabled_display = ", ".join(enabled_entitlements) or "none"
        print_dev_seed(f"Organization: {result.organization_id}")
        print_dev_seed(f"Workspace: {result.workspace_id}")
        print_dev_seed(f"Default tier: {result.default_tier_id}")
        print_dev_seed(f"Default tier entitlements: {enabled_display}")
    except Exception as e:
        print_error(str(e))
        raise typer.Exit(1) from None
