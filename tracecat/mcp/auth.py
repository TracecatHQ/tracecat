"""MCP server authentication and user resolution."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from tracecat.auth.oidc import get_platform_oidc_config
from tracecat.auth.types import Role
from tracecat.authz.enums import OrgRole, WorkspaceRole
from tracecat.contexts import ctx_role
from tracecat.db.engine import get_async_session_context_manager
from tracecat.db.models import (
    Membership,
    Organization,
    OrganizationMembership,
    User,
    Workspace,
)
from tracecat.mcp.config import TRACECAT_MCP__BASE_URL

if TYPE_CHECKING:
    from fastmcp.server.auth import AuthProvider

    from tracecat.identifiers import OrganizationID, UserID, WorkspaceID


def create_mcp_auth() -> AuthProvider:
    """Build OIDCProxy auth provider from platform OIDC config.

    Uses OIDCProxy which handles DCR, authorization, and token exchange locally
    while proxying to the upstream OIDC provider. Works with any OIDC-compliant
    provider including PropelAuth, Google, Okta, etc.
    """
    from fastmcp.server.auth.oidc_proxy import OIDCProxy

    oidc_config = get_platform_oidc_config()

    if not oidc_config.issuer:
        raise ValueError(
            "OIDC_ISSUER must be configured for the MCP server. "
            "Set OIDC_ISSUER to your OIDC provider's issuer URL."
        )

    if not TRACECAT_MCP__BASE_URL:
        raise ValueError(
            "TRACECAT_MCP__BASE_URL must be configured for the MCP server. "
            "Set it to the public URL where the MCP server is accessible."
        )

    config_url = f"{oidc_config.issuer}/.well-known/openid-configuration"

    return OIDCProxy(
        config_url=config_url,
        client_id=oidc_config.client_id,
        client_secret=oidc_config.client_secret,
        base_url=TRACECAT_MCP__BASE_URL,
    )


async def resolve_user_by_email(email: str) -> User:
    """Look up a user by email, raising if not found."""
    async with get_async_session_context_manager() as session:
        result = await session.execute(
            select(User).where(User.email == email)  # pyright: ignore[reportArgumentType]
        )
        user = result.scalars().first()
        if user is None:
            raise ValueError(f"No user found for email: {email}")
        return user


async def resolve_org_membership(
    user_id: UserID,
    organization_id: OrganizationID,
) -> OrgRole:
    """Get the user's role in a specific organization.

    Args:
        user_id: The user to look up.
        organization_id: The organization to check membership in.

    Returns:
        The user's OrgRole in the specified organization.

    Raises:
        ValueError: If the user has no membership in the organization.
    """
    async with get_async_session_context_manager() as session:
        result = await session.execute(
            select(OrganizationMembership).where(
                OrganizationMembership.user_id == user_id,
                OrganizationMembership.organization_id == organization_id,
            )
        )
        membership = result.scalars().first()
        if membership is None:
            raise ValueError(
                f"User {user_id} has no membership in organization {organization_id}"
            )
        return membership.role


async def resolve_workspace_org(workspace_id: WorkspaceID) -> OrganizationID:
    """Look up which organization a workspace belongs to.

    Raises:
        ValueError: If the workspace does not exist.
    """
    async with get_async_session_context_manager() as session:
        result = await session.execute(
            select(Workspace.organization_id).where(Workspace.id == workspace_id)
        )
        org_id = result.scalar_one_or_none()
        if org_id is None:
            raise ValueError(f"Workspace {workspace_id} not found")
        return org_id


async def resolve_workspace_membership(
    user_id: UserID,
    workspace_id: WorkspaceID,
) -> WorkspaceRole:
    """Verify user has access to workspace and return their role."""
    async with get_async_session_context_manager() as session:
        result = await session.execute(
            select(Membership).where(
                Membership.user_id == user_id,
                Membership.workspace_id == workspace_id,
            )
        )
        membership = result.scalars().first()
        if membership is None:
            raise ValueError(
                f"User {user_id} does not have access to workspace {workspace_id}"
            )
        return membership.role


async def resolve_role(email: str, workspace_id: WorkspaceID) -> Role:
    """Resolve a user's Role for a given workspace from their OAuth email.

    Pipeline: email → User → Workspace.organization_id → OrganizationMembership → Membership → Role

    The workspace's owning organization is resolved first, then the user's
    membership in *that* organization is checked. This prevents an admin in
    org A from gaining access to a workspace belonging to org B.
    """
    user = await resolve_user_by_email(email)
    org_id = await resolve_workspace_org(workspace_id)
    org_role = await resolve_org_membership(user.id, org_id)

    # Org admins/owners can access all workspaces in their org without explicit membership
    if org_role in (OrgRole.OWNER, OrgRole.ADMIN):
        workspace_role = WorkspaceRole.ADMIN
    else:
        workspace_role = await resolve_workspace_membership(user.id, workspace_id)

    role = Role(
        type="user",
        user_id=user.id,
        workspace_id=workspace_id,
        organization_id=org_id,
        workspace_role=workspace_role,
        org_role=org_role,
        service_id="tracecat-mcp",
    )
    # Set context variable so downstream services that rely on ctx_role
    # (e.g. SecretsService.with_session()) can resolve the role automatically.
    ctx_role.set(role)
    return role


async def list_user_workspaces(
    email: str,
) -> list[dict[str, str]]:
    """List all workspaces accessible to a user.

    Includes:
    - Workspaces with explicit Membership rows.
    - All workspaces in orgs where the user is an owner or admin (implicit access).
    """
    from sqlalchemy import cast, literal, union

    user = await resolve_user_by_email(email)
    async with get_async_session_context_manager() as session:
        # Explicit workspace memberships
        explicit_q = (
            select(
                Workspace.id,
                Workspace.name,
                Membership.role.label("role"),
            )
            .join(Membership, Membership.workspace_id == Workspace.id)
            .where(Membership.user_id == user.id)
        )

        # Implicit access: org admins/owners see all workspaces in their orgs
        # Cast the literal to the workspacerole enum so the UNION types match.
        implicit_q = (
            select(
                Workspace.id,
                Workspace.name,
                cast(
                    literal(WorkspaceRole.ADMIN.name),
                    Membership.role.type,
                ).label("role"),
            )
            .join(
                OrganizationMembership,
                OrganizationMembership.organization_id == Workspace.organization_id,
            )
            .where(
                OrganizationMembership.user_id == user.id,
                OrganizationMembership.role.in_([OrgRole.OWNER, OrgRole.ADMIN]),
            )
        )

        combined = union(explicit_q, implicit_q).subquery()
        result = await session.execute(select(combined))
        return [
            {"id": str(row.id), "name": row.name, "role": row.role}
            for row in result.all()
        ]


async def list_user_organizations(
    email: str,
) -> list[dict[str, str]]:
    """List all organizations a user belongs to."""
    user = await resolve_user_by_email(email)
    async with get_async_session_context_manager() as session:
        result = await session.execute(
            select(Organization, OrganizationMembership.role)
            .join(
                OrganizationMembership,
                OrganizationMembership.organization_id == Organization.id,
            )
            .where(OrganizationMembership.user_id == user.id)
        )
        return [
            {"id": str(org.id), "name": org.name, "role": role.value}
            for org, role in result.all()
        ]


def get_email_from_token() -> str:
    """Extract user email from the current MCP access token."""
    from fastmcp.server.dependencies import get_access_token

    access_token = get_access_token()
    if access_token is None:
        raise ValueError("Authentication required")

    claims = access_token.claims
    email = claims.get("email")
    if not email:
        raise ValueError("Token does not contain an email claim")
    return email
