"""Admin commands for user and superuser management."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from functools import wraps
from typing import Annotated, Any

import typer

from tracecat_admin.client import AdminClient, AdminClientError
from tracecat_admin.output import (
    print_error,
    print_invitation_detail,
    print_invitations_table,
    print_invite_result,
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


# Invite subcommand group
invite_app = typer.Typer(no_args_is_help=True)
app.add_typer(invite_app, name="invite", help="Invitation management.")


@invite_app.command("org", no_args_is_help=True)
@async_command
async def invite_org(
    email: Annotated[
        str, typer.Option("--email", "-e", help="Email address to invite")
    ],
    role: Annotated[
        str,
        typer.Option(
            "--role",
            "-r",
            help="Role to assign (owner, admin, member)",
        ),
    ] = "admin",
    name: Annotated[
        str | None,
        typer.Option(
            "--name",
            "-n",
            help="Organization name. Creates org if it doesn't exist.",
        ),
    ] = None,
    slug: Annotated[
        str | None,
        typer.Option(
            "--slug",
            "-s",
            help="Organization slug. If not provided, uses 'default' or 'default-N'.",
        ),
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", "-j", help="Output as JSON")
    ] = False,
) -> None:
    """Invite a user to an organization.

    If the organization doesn't exist, creates it first.
    Sends an invitation email with a magic link.

    Examples:

        # Invite to default organization
        tracecat admin invite org --email admin@example.com --role admin

        # Invite to a specific organization (creates if doesn't exist)
        tracecat admin invite org --email admin@acme.com --role admin --name "Acme Corp" --slug acme
    """
    # Validate role
    valid_roles = {"owner", "admin", "member"}
    if role.lower() not in valid_roles:
        print_error(f"Invalid role '{role}'. Must be one of: {', '.join(valid_roles)}")
        raise typer.Exit(1)

    try:
        async with AdminClient() as client:
            result = await client.invite_org_user(
                email=email,
                role=role.lower(),
                org_name=name,
                org_slug=slug,
            )

        if json_output:
            import json

            typer.echo(json.dumps(result.model_dump(mode="json"), indent=2))
        else:
            print_invite_result(result)
    except AdminClientError as e:
        print_error(str(e))
        raise typer.Exit(1) from None


@invite_app.command("list", no_args_is_help=True)
@async_command
async def list_invitations(
    org_id: Annotated[
        str, typer.Option("--org-id", "-o", help="Organization ID (UUID)")
    ],
    json_output: Annotated[
        bool, typer.Option("--json", "-j", help="Output as JSON")
    ] = False,
) -> None:
    """List all invitations for an organization.

    Examples:

        tracecat admin invite list --org-id 123e4567-e89b-12d3-a456-426614174000
    """
    try:
        async with AdminClient() as client:
            invitations = await client.list_org_invitations(org_id)

        if json_output:
            import json

            typer.echo(
                json.dumps(
                    [inv.model_dump(mode="json") for inv in invitations], indent=2
                )
            )
        else:
            if not invitations:
                typer.echo("No invitations found.")
            else:
                print_invitations_table(invitations)
    except AdminClientError as e:
        print_error(str(e))
        raise typer.Exit(1) from None


@invite_app.command("revoke", no_args_is_help=True)
@async_command
async def revoke_invitation(
    org_id: Annotated[
        str, typer.Option("--org-id", "-o", help="Organization ID (UUID)")
    ],
    invitation_id: Annotated[
        str, typer.Option("--invitation-id", "-i", help="Invitation ID (UUID)")
    ],
    json_output: Annotated[
        bool, typer.Option("--json", "-j", help="Output as JSON")
    ] = False,
) -> None:
    """Revoke a pending invitation.

    Examples:

        tracecat admin invite revoke --org-id 123e4567-... --invitation-id 987f6543-...
    """
    try:
        async with AdminClient() as client:
            result = await client.revoke_org_invitation(org_id, invitation_id)

        if json_output:
            import json

            typer.echo(json.dumps(result.model_dump(mode="json"), indent=2))
        else:
            print_success(f"Invitation for '{result.email}' has been revoked.")
            print_invitation_detail(result)
    except AdminClientError as e:
        print_error(str(e))
        raise typer.Exit(1) from None
