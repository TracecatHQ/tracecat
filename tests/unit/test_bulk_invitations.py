"""Tests for bulk invitations + email configuration gating."""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config
from tracecat.auth.schemas import UserRole
from tracecat.auth.types import Role
from tracecat.authz.scopes import ORG_ADMIN_SCOPES, ORG_OWNER_SCOPES
from tracecat.db.models import (
    Invitation,
    Organization,
    OrganizationInvitation,
    OrganizationMembership,
    RoleScope,
    Scope,
    User,
    Workspace,
)
from tracecat.db.models import Role as DBRole
from tracecat.email import build_accept_url, is_email_configured
from tracecat.email.templates import render_invitation_email
from tracecat.exceptions import TracecatAuthorizationError, TracecatValidationError
from tracecat.invitations.enums import InvitationStatus
from tracecat.invitations.types import MAX_BULK_INVITE_EMAILS, BatchInviteStatus
from tracecat.organization.service import OrgService
from tracecat.workspaces.schemas import WorkspaceInvitationCreate
from tracecat.workspaces.service import WorkspaceService


@pytest.fixture
async def org(session: AsyncSession) -> Organization:
    org = Organization(
        id=uuid.uuid4(),
        name="Bulk Org",
        slug=f"bulk-org-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    session.add(org)
    await session.commit()
    return org


@pytest.fixture
async def admin(session: AsyncSession, org: Organization) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"admin-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="hashed",
        role=UserRole.BASIC,
        is_active=True,
        is_superuser=False,
        is_verified=True,
    )
    session.add(user)
    await session.flush()
    session.add(OrganizationMembership(user_id=user.id, organization_id=org.id))
    await session.commit()
    return user


@pytest.fixture
async def member_role(session: AsyncSession, org: Organization) -> DBRole:
    role = DBRole(
        id=uuid.uuid4(),
        name="Organization Member",
        slug="organization-member",
        organization_id=org.id,
    )
    session.add(role)
    await session.commit()
    return role


@pytest.fixture
async def owner_role(session: AsyncSession, org: Organization) -> DBRole:
    # Wired with owner-only scopes that an org admin lacks, so the scope-subset
    # escalation guard blocks a non-owner from inviting at this role.
    return await _make_role_with_scopes(
        session,
        org.id,
        name="Organization Owner",
        slug="organization-owner",
        scope_names=set(ORG_OWNER_SCOPES) - set(ORG_ADMIN_SCOPES),
    )


def _admin_role(org_id: uuid.UUID, user_id: uuid.UUID) -> Role:
    return Role(
        type="user",
        user_id=user_id,
        organization_id=org_id,
        service_id="tracecat-api",
        is_platform_superuser=False,
        scopes=ORG_ADMIN_SCOPES,
    )


class TestBatchCreateInvitations:
    @pytest.mark.anyio
    async def test_partial_success_created_and_skipped(
        self,
        session: AsyncSession,
        org: Organization,
        admin: User,
        member_role: DBRole,
    ) -> None:
        """New emails are created; an existing member is skipped."""
        # An existing member that should be skipped.
        existing = User(
            id=uuid.uuid4(),
            email=f"existing-{uuid.uuid4().hex[:8]}@example.com",
            hashed_password="hashed",
            role=UserRole.BASIC,
            is_active=True,
            is_superuser=False,
            is_verified=True,
        )
        session.add(existing)
        await session.flush()
        session.add(OrganizationMembership(user_id=existing.id, organization_id=org.id))
        await session.commit()

        service = OrgService(session, role=_admin_role(org.id, admin.id))
        items = await service.batch_create_invitations(
            emails=["new1@example.com", existing.email, "new2@example.com"],
            role_id=member_role.id,
        )

        by_email = {i.email: i for i in items}
        assert by_email["new1@example.com"].status == BatchInviteStatus.CREATED
        assert by_email["new2@example.com"].status == BatchInviteStatus.CREATED
        assert by_email[existing.email.lower()].status == BatchInviteStatus.SKIPPED
        assert "member" in (by_email[existing.email.lower()].reason or "").lower()

        # Created invites persist.
        result = await session.execute(
            select(OrganizationInvitation).where(
                OrganizationInvitation.organization_id == org.id
            )
        )
        emails = {inv.email for inv in result.scalars().all()}
        assert {"new1@example.com", "new2@example.com"} <= emails

    @pytest.mark.anyio
    async def test_normalize_and_dedup(
        self,
        session: AsyncSession,
        org: Organization,
        admin: User,
        member_role: DBRole,
    ) -> None:
        """Mixed-case duplicates collapse to a single created invite."""
        service = OrgService(session, role=_admin_role(org.id, admin.id))
        items = await service.batch_create_invitations(
            emails=["Dup@Example.com", "dup@example.com", " dup@example.com "],
            role_id=member_role.id,
        )
        assert len(items) == 1
        assert items[0].email == "dup@example.com"
        assert items[0].status == BatchInviteStatus.CREATED

    @pytest.mark.anyio
    async def test_live_pending_invite_is_skipped_not_rewritten(
        self,
        session: AsyncSession,
        org: Organization,
        admin: User,
        member_role: DBRole,
    ) -> None:
        """A second bulk call leaves a live pending invite untouched."""
        service = OrgService(session, role=_admin_role(org.id, admin.id))
        first = await service.batch_create_invitations(
            emails=["pending@example.com"], role_id=member_role.id
        )
        original_token = first[0].token

        second = await service.batch_create_invitations(
            emails=["pending@example.com"], role_id=member_role.id
        )
        assert second[0].status == BatchInviteStatus.SKIPPED

        result = await session.execute(
            select(OrganizationInvitation).where(
                OrganizationInvitation.email == "pending@example.com"
            )
        )
        invite = result.scalar_one()
        assert invite.token == original_token  # not rewritten

    @pytest.mark.anyio
    async def test_expired_invite_is_refreshed(
        self,
        session: AsyncSession,
        org: Organization,
        admin: User,
        member_role: DBRole,
    ) -> None:
        """A stale (revoked) invite is refreshed with a new token on re-invite."""
        service = OrgService(session, role=_admin_role(org.id, admin.id))
        first = await service.batch_create_invitations(
            emails=["stale@example.com"], role_id=member_role.id
        )
        original_token = first[0].token

        # Revoke it (stale -> eligible for refresh).
        result = await session.execute(
            select(OrganizationInvitation).where(
                OrganizationInvitation.email == "stale@example.com"
            )
        )
        invite = result.scalar_one()
        invite.status = InvitationStatus.REVOKED
        await session.commit()

        second = await service.batch_create_invitations(
            emails=["stale@example.com"], role_id=member_role.id
        )
        assert second[0].status == BatchInviteStatus.CREATED
        assert second[0].token != original_token

    @pytest.mark.anyio
    async def test_bulk_rejects_oversized_request(
        self,
        session: AsyncSession,
        org: Organization,
        admin: User,
        member_role: DBRole,
    ) -> None:
        """A request over the cap is rejected defensively in the service."""
        service = OrgService(session, role=_admin_role(org.id, admin.id))
        emails = [f"user{i}@example.com" for i in range(MAX_BULK_INVITE_EMAILS + 1)]
        with pytest.raises(TracecatValidationError, match="more than"):
            await service.batch_create_invitations(
                emails=emails, role_id=member_role.id
            )

    @pytest.mark.anyio
    async def test_bulk_does_not_duplicate_mixed_case_single_invite(
        self,
        session: AsyncSession,
        org: Organization,
        admin: User,
        member_role: DBRole,
    ) -> None:
        """A mixed-case single invite is refreshed, not duplicated, by bulk.

        Single-invite normalizes on write, so the bulk upsert's case-sensitive
        unique constraint matches and there is exactly one row.
        """
        service = OrgService(session, role=_admin_role(org.id, admin.id))

        # Single invite with mixed case persists lowercased.
        single = await service.create_invitation(
            email="Mixed@Example.com", role_id=member_role.id
        )
        assert single.email == "mixed@example.com"

        # Bulk re-invite of the same address (lowercased) leaves the live
        # pending invite untouched rather than creating a second row.
        items = await service.batch_create_invitations(
            emails=["mixed@example.com"], role_id=member_role.id
        )
        assert items[0].status == BatchInviteStatus.SKIPPED

        result = await session.execute(
            select(OrganizationInvitation).where(
                OrganizationInvitation.organization_id == org.id,
                OrganizationInvitation.email == "mixed@example.com",
            )
        )
        assert len(result.scalars().all()) == 1

    @pytest.mark.anyio
    async def test_owner_role_escalation_raises_for_whole_request(
        self,
        session: AsyncSession,
        org: Organization,
        admin: User,
        owner_role: DBRole,
    ) -> None:
        """Assigning the owner role without owner scope fails the whole request."""
        service = OrgService(session, role=_admin_role(org.id, admin.id))
        with pytest.raises(TracecatAuthorizationError):
            await service.batch_create_invitations(
                emails=["x@example.com"], role_id=owner_role.id
            )

    @pytest.mark.anyio
    async def test_owner_with_owner_scope_can_assign_owner_role(
        self,
        session: AsyncSession,
        org: Organization,
        admin: User,
        owner_role: DBRole,
    ) -> None:
        owner_principal = Role(
            type="user",
            user_id=admin.id,
            organization_id=org.id,
            service_id="tracecat-api",
            is_platform_superuser=False,
            scopes=ORG_OWNER_SCOPES,
        )
        service = OrgService(session, role=owner_principal)
        items = await service.batch_create_invitations(
            emails=["newowner@example.com"], role_id=owner_role.id
        )
        assert items[0].status == BatchInviteStatus.CREATED


class TestEmailConfiguration:
    def test_not_configured_without_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(config, "TRACECAT__RESEND_API_KEY", "")
        monkeypatch.setattr(config, "TRACECAT__RESEND_FROM_EMAIL", "")
        assert is_email_configured() is False

    def test_not_configured_with_key_but_no_from(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(config, "TRACECAT__RESEND_API_KEY", "re_x")
        monkeypatch.setattr(config, "TRACECAT__RESEND_FROM_EMAIL", "")
        assert is_email_configured() is False

    def test_configured_with_both(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(config, "TRACECAT__RESEND_API_KEY", "re_x")
        monkeypatch.setattr(
            config, "TRACECAT__RESEND_FROM_EMAIL", "invites@example.com"
        )
        assert is_email_configured() is True

    def test_build_accept_url_uses_public_app_url(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            config, "TRACECAT__PUBLIC_APP_URL", "https://app.example.com"
        )
        assert (
            build_accept_url("tok123")
            == "https://app.example.com/invitations/accept?token=tok123"
        )


class TestEmailTemplateEscaping:
    def test_admin_controlled_name_is_escaped_in_html(self) -> None:
        """A malicious org/workspace name cannot inject markup into the HTML."""
        _, html, _ = render_invitation_email(
            accept_url="https://app.example.com/invitations/accept?token=tok",
            context_name='<script>alert(1)</script>"Acme"',
            kind="organization",
        )
        # The raw markup must not appear; the escaped form must.
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html

    def test_accept_url_is_escaped_in_html(self) -> None:
        """The accept URL is escaped where it lands inside HTML attributes."""
        _, html, _ = render_invitation_email(
            accept_url='https://app.example.com/accept?token=a&b="x',
            context_name="Acme",
            kind="workspace",
        )
        # Ampersand and quote are escaped in the href context.
        assert "token=a&amp;b=&quot;x" in html

    def test_plaintext_keeps_raw_url(self) -> None:
        """The plaintext body keeps the raw URL (no HTML escaping)."""
        url = "https://app.example.com/accept?token=a&b=c"
        _, _, text = render_invitation_email(
            accept_url=url, context_name="Acme", kind="organization"
        )
        assert url in text


async def _make_role_with_scopes(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    name: str,
    slug: str | None,
    scope_names: set[str],
) -> DBRole:
    """Create a DB role wired to the given scope names."""
    from tracecat.authz.enums import ScopeSource

    role = DBRole(id=uuid.uuid4(), name=name, slug=slug, organization_id=org_id)
    session.add(role)
    await session.flush()
    for scope_name in scope_names:
        resource, _, action = scope_name.rpartition(":")
        scope = Scope(
            id=uuid.uuid4(),
            name=scope_name,
            resource=resource or scope_name,
            action=action or "execute",
            source=ScopeSource.CUSTOM,
            organization_id=org_id,
        )
        session.add(scope)
        await session.flush()
        session.add(RoleScope(role_id=role.id, scope_id=scope.id))
    await session.commit()
    return role


class TestWorkspaceInviteRoleEscalation:
    @pytest.fixture
    async def workspace(self, session: AsyncSession, org: Organization) -> Workspace:
        ws = Workspace(id=uuid.uuid4(), name="WS", organization_id=org.id)
        session.add(ws)
        await session.commit()
        return ws

    def _inviter_role(
        self, org_id: uuid.UUID, user_id: uuid.UUID, scopes: set[str]
    ) -> Role:
        return Role(
            type="user",
            user_id=user_id,
            organization_id=org_id,
            service_id="tracecat-api",
            is_platform_superuser=False,
            scopes=frozenset(scopes),
        )

    @pytest.mark.anyio
    async def test_rejects_role_with_scope_inviter_lacks(
        self,
        session: AsyncSession,
        org: Organization,
        admin: User,
        workspace: Workspace,
    ) -> None:
        elevated = await _make_role_with_scopes(
            session,
            org.id,
            name="WS Admin",
            slug=None,
            scope_names={"workspace:member:invite", "workspace:member:remove"},
        )
        # Inviter lacks workspace:member:remove.
        service = WorkspaceService(
            session,
            role=self._inviter_role(
                org.id, admin.id, {"workspace:member:invite", "workspace:read"}
            ),
        )
        with pytest.raises(TracecatAuthorizationError):
            await service.batch_create_invitations(
                workspace.id,
                emails=["x@example.com"],
                role_id=str(elevated.id),
            )

    @pytest.mark.anyio
    async def test_allows_role_within_inviter_scopes(
        self,
        session: AsyncSession,
        org: Organization,
        admin: User,
        workspace: Workspace,
    ) -> None:
        subset = await _make_role_with_scopes(
            session,
            org.id,
            name="WS Viewer",
            slug=None,
            scope_names={"workspace:read"},
        )
        service = WorkspaceService(
            session,
            role=self._inviter_role(
                org.id, admin.id, {"workspace:member:invite", "workspace:read"}
            ),
        )
        items = await service.batch_create_invitations(
            workspace.id, emails=["y@example.com"], role_id=str(subset.id)
        )
        assert items[0].status == BatchInviteStatus.CREATED

    @pytest.mark.anyio
    async def test_superuser_bypasses_escalation_check(
        self,
        session: AsyncSession,
        org: Organization,
        admin: User,
        workspace: Workspace,
    ) -> None:
        elevated = await _make_role_with_scopes(
            session,
            org.id,
            name="WS Admin 2",
            slug=None,
            scope_names={"workspace:member:invite", "workspace:member:remove"},
        )
        # Platform superusers present with the "*" scope.
        superuser = Role(
            type="user",
            user_id=admin.id,
            organization_id=org.id,
            service_id="tracecat-api",
            is_platform_superuser=True,
            scopes=frozenset({"*"}),
        )
        service = WorkspaceService(session, role=superuser)
        items = await service.batch_create_invitations(
            workspace.id, emails=["z@example.com"], role_id=str(elevated.id)
        )
        assert items[0].status == BatchInviteStatus.CREATED


class TestWorkspaceBulkInvite:
    @pytest.fixture
    async def workspace(self, session: AsyncSession, org: Organization) -> Workspace:
        ws = Workspace(id=uuid.uuid4(), name="WS Bulk", organization_id=org.id)
        session.add(ws)
        await session.commit()
        return ws

    @pytest.fixture
    async def viewer_role(self, session: AsyncSession, org: Organization) -> DBRole:
        return await _make_role_with_scopes(
            session,
            org.id,
            name="WS Viewer Bulk",
            slug=None,
            scope_names={"workspace:read"},
        )

    @pytest.mark.anyio
    async def test_service_account_actor_can_bulk_invite(
        self,
        session: AsyncSession,
        org: Organization,
        workspace: Workspace,
        viewer_role: DBRole,
    ) -> None:
        """API-key (service-account) actors may bulk invite, matching single.

        Single invite permits invited_by=None; bulk now does too instead of
        rejecting on a missing user_id.
        """
        service_account = Role(
            type="service_account",
            service_account_id=uuid.uuid4(),
            workspace_id=workspace.id,
            bound_workspace_id=workspace.id,
            organization_id=org.id,
            service_id="tracecat-api",
            scopes=frozenset({"workspace:member:invite", "workspace:read"}),
        )
        service = WorkspaceService(session, role=service_account)
        items = await service.batch_create_invitations(
            workspace.id, emails=["svc@example.com"], role_id=str(viewer_role.id)
        )
        assert items[0].status == BatchInviteStatus.CREATED

        result = await session.execute(
            select(Invitation).where(
                Invitation.workspace_id == workspace.id,
                Invitation.email == "svc@example.com",
            )
        )
        invite = result.scalar_one()
        assert invite.invited_by is None

    @pytest.mark.anyio
    async def test_bulk_does_not_duplicate_mixed_case_single_invite(
        self,
        session: AsyncSession,
        org: Organization,
        admin: User,
        workspace: Workspace,
        viewer_role: DBRole,
    ) -> None:
        """A mixed-case single workspace invite is not duplicated by bulk."""
        inviter = Role(
            type="user",
            user_id=admin.id,
            organization_id=org.id,
            workspace_id=workspace.id,
            service_id="tracecat-api",
            scopes=frozenset({"workspace:member:invite", "workspace:read"}),
        )
        service = WorkspaceService(session, role=inviter)

        single = await service.create_invitation(
            workspace.id,
            WorkspaceInvitationCreate(
                email="Mixed@Example.com", role_id=str(viewer_role.id)
            ),
        )
        assert single.email == "mixed@example.com"

        items = await service.batch_create_invitations(
            workspace.id, emails=["mixed@example.com"], role_id=str(viewer_role.id)
        )
        assert items[0].status == BatchInviteStatus.SKIPPED

        result = await session.execute(
            select(Invitation).where(
                Invitation.workspace_id == workspace.id,
                Invitation.email == "mixed@example.com",
            )
        )
        assert len(result.scalars().all()) == 1
