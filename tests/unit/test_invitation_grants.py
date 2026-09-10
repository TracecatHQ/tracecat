"""Tests for multi-grant invitations and what accepting them assigns."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.support.membership import (
    grant_org_membership,
    grant_org_membership_via_group,
)
from tracecat.auth.schemas import UserRole
from tracecat.auth.types import Role
from tracecat.authz.scopes import ORG_ADMIN_SCOPES
from tracecat.authz.seeding import seed_system_roles_for_org, seed_system_scopes
from tracecat.db.models import (
    Invitation,
    Organization,
    RoleScope,
    Scope,
    User,
    UserRoleAssignment,
    Workspace,
)
from tracecat.db.models import (
    InvitationGrant as InvitationGrantRow,
)
from tracecat.db.models import Role as DBRole
from tracecat.exceptions import (
    TracecatAuthorizationError,
    TracecatValidationError,
)
from tracecat.invitations.enums import InvitationStatus
from tracecat.invitations.schemas import InvitationCreate, InvitationGrant
from tracecat.invitations.service import (
    InvitationService,
    accept_invitation_for_user,
    get_pending_invitation_for_email,
)
from tracecat.organization.router import list_org_members


@pytest.fixture
async def org(session: AsyncSession) -> Organization:
    organization = Organization(
        id=uuid.uuid4(),
        name="Grants Org",
        slug=f"grants-org-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    session.add(organization)
    await session.flush()
    await seed_system_scopes(session)
    await seed_system_roles_for_org(session, organization.id)
    await session.commit()
    return organization


@pytest.fixture
async def other_org(session: AsyncSession) -> Organization:
    organization = Organization(
        id=uuid.uuid4(),
        name="Other Org",
        slug=f"other-org-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    session.add(organization)
    await session.flush()
    await seed_system_roles_for_org(session, organization.id)
    await session.commit()
    return organization


@pytest.fixture
async def admin(session: AsyncSession, org: Organization) -> User:
    """An admin backed by a real org-wide assignment, so ceilings see scopes."""
    user = User(
        id=uuid.uuid4(),
        email=f"admin-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="hashed",
        role=UserRole.ADMIN,
        is_active=True,
        is_superuser=False,
        is_verified=True,
    )
    session.add(user)
    await session.flush()

    admin_db_role = DBRole(
        id=uuid.uuid4(),
        name="Grants Admin",
        slug=None,
        organization_id=org.id,
    )
    session.add(admin_db_role)
    await session.flush()
    scopes = (
        await session.execute(
            select(Scope).where(Scope.name.in_(sorted(ORG_ADMIN_SCOPES)))
        )
    ).scalars()
    for scope in scopes.all():
        session.add(RoleScope(role_id=admin_db_role.id, scope_id=scope.id))
    session.add(
        UserRoleAssignment(
            organization_id=org.id,
            user_id=user.id,
            workspace_id=None,
            role_id=admin_db_role.id,
        )
    )
    await session.commit()
    return user


@pytest.fixture
async def invitee(session: AsyncSession) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"invitee-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="hashed",
        role=UserRole.BASIC,
        is_active=True,
        is_superuser=False,
        is_verified=True,
    )
    session.add(user)
    await session.commit()
    return user


@pytest.fixture
async def workspace_a(session: AsyncSession, org: Organization) -> Workspace:
    return await _workspace(session, org.id, "ws-a")


@pytest.fixture
async def workspace_b(session: AsyncSession, org: Organization) -> Workspace:
    return await _workspace(session, org.id, "ws-b")


async def _workspace(
    session: AsyncSession, organization_id: uuid.UUID, name: str
) -> Workspace:
    workspace = Workspace(
        id=uuid.uuid4(),
        name=f"{name}-{uuid.uuid4().hex[:6]}",
        organization_id=organization_id,
    )
    session.add(workspace)
    await session.commit()
    return workspace


async def _role_id(
    session: AsyncSession, organization_id: uuid.UUID, slug: str
) -> uuid.UUID:
    return (
        await session.execute(
            select(DBRole.id).where(
                DBRole.organization_id == organization_id,
                DBRole.slug == slug,
            )
        )
    ).scalar_one()


def _admin_role(organization_id: uuid.UUID, user_id: uuid.UUID) -> Role:
    return Role(
        type="user",
        user_id=user_id,
        organization_id=organization_id,
        service_id="tracecat-api",
        is_platform_superuser=False,
        scopes=ORG_ADMIN_SCOPES,
    )


async def _assignments(
    session: AsyncSession, user_id: uuid.UUID, organization_id: uuid.UUID
) -> dict[uuid.UUID | None, uuid.UUID]:
    """Map each scope the user holds an assignment at to its role id."""
    rows = (
        await session.execute(
            select(UserRoleAssignment.workspace_id, UserRoleAssignment.role_id).where(
                UserRoleAssignment.user_id == user_id,
                UserRoleAssignment.organization_id == organization_id,
            )
        )
    ).tuples()
    return dict(rows.all())


class TestCreateInvitationGrants:
    """Validation rules applied when an invitation is created."""

    @pytest.mark.anyio
    async def test_create_with_org_and_workspace_grants(
        self,
        session: AsyncSession,
        org: Organization,
        admin: User,
        workspace_a: Workspace,
        workspace_b: Workspace,
    ):
        """An invitation persists every grant it was given."""
        service = InvitationService(session, role=_admin_role(org.id, admin.id))
        member_role_id = await _role_id(session, org.id, "organization-member")
        editor_role_id = await _role_id(session, org.id, "workspace-editor")

        invitation = await service.create_invitation(
            InvitationCreate(
                email="multi@example.com",
                grants=[
                    InvitationGrant(role_id=member_role_id),
                    InvitationGrant(
                        workspace_id=workspace_a.id, role_id=editor_role_id
                    ),
                    InvitationGrant(
                        workspace_id=workspace_b.id, role_id=editor_role_id
                    ),
                ],
            )
        )

        assert invitation.organization_id == org.id
        assert len(invitation.grants) == 3
        assert {g.workspace_id for g in invitation.grants} == {
            None,
            workspace_a.id,
            workspace_b.id,
        }

        # The members listing carries the grants on the invited row.
        members = await list_org_members(
            role=_admin_role(org.id, admin.id), session=session
        )
        invited = next(m for m in members if m.email == "multi@example.com")
        assert len(invited.grants) == 3

    @pytest.mark.anyio
    async def test_workspace_only_invitation_belongs_to_org(
        self,
        session: AsyncSession,
        org: Organization,
        admin: User,
        workspace_a: Workspace,
    ):
        """A workspace-only invitation still belongs to the org."""
        service = InvitationService(session, role=_admin_role(org.id, admin.id))
        editor_role_id = await _role_id(session, org.id, "workspace-editor")

        invitation = await service.create_invitation(
            InvitationCreate(
                email="ws-only@example.com",
                grants=[
                    InvitationGrant(workspace_id=workspace_a.id, role_id=editor_role_id)
                ],
            )
        )

        assert invitation.organization_id == org.id
        assert invitation.grants[0].workspace_id == workspace_a.id
        assert len(invitation.grants) == 1

    @pytest.mark.anyio
    async def test_workspace_from_another_org_rejected(
        self,
        session: AsyncSession,
        org: Organization,
        other_org: Organization,
        admin: User,
    ):
        """Every workspace in the grants must belong to the inviting org."""
        service = InvitationService(session, role=_admin_role(org.id, admin.id))
        editor_role_id = await _role_id(session, org.id, "workspace-editor")
        foreign_workspace = await _workspace(session, other_org.id, "foreign")

        with pytest.raises(TracecatValidationError, match="Workspace not found"):
            await service.create_invitation(
                InvitationCreate(
                    email="foreign@example.com",
                    grants=[
                        InvitationGrant(
                            workspace_id=foreign_workspace.id, role_id=editor_role_id
                        )
                    ],
                )
            )

    @pytest.mark.anyio
    async def test_duplicate_pending_rejected(
        self, session: AsyncSession, org: Organization, admin: User
    ):
        """A second pending invitation for the same email is rejected."""
        service = InvitationService(session, role=_admin_role(org.id, admin.id))
        member_role_id = await _role_id(session, org.id, "organization-member")
        params = InvitationCreate(
            email="dupe@example.com",
            grants=[InvitationGrant(role_id=member_role_id)],
        )

        await service.create_invitation(params)
        with pytest.raises(
            TracecatValidationError, match="An invitation already exists"
        ):
            await service.create_invitation(params)

    @pytest.mark.anyio
    async def test_expired_invitation_replaced(
        self, session: AsyncSession, org: Organization, admin: User
    ):
        """An expired invitation is replaced rather than blocking a new one."""
        service = InvitationService(session, role=_admin_role(org.id, admin.id))
        member_role_id = await _role_id(session, org.id, "organization-member")
        params = InvitationCreate(
            email="expired@example.com",
            grants=[InvitationGrant(role_id=member_role_id)],
        )

        first = await service.create_invitation(params)
        old_id = first.id
        first.expires_at = datetime.now(UTC) - timedelta(days=1)
        await session.commit()

        second = await service.create_invitation(params)

        assert second.id != old_id
        assert second.expires_at > datetime.now(UTC)
        assert (
            await session.execute(select(Invitation).where(Invitation.id == old_id))
        ).scalar_one_or_none() is None

    @pytest.mark.anyio
    async def test_existing_member_rejected(
        self, session: AsyncSession, org: Organization, admin: User, invitee: User
    ):
        """An email that is already an org member cannot be invited."""
        await grant_org_membership(session, user_id=invitee.id, organization_id=org.id)
        await session.commit()
        service = InvitationService(session, role=_admin_role(org.id, admin.id))
        member_role_id = await _role_id(session, org.id, "organization-member")

        with pytest.raises(TracecatValidationError, match="already a member"):
            await service.create_invitation(
                InvitationCreate(
                    email=invitee.email,
                    grants=[InvitationGrant(role_id=member_role_id)],
                )
            )

    @pytest.mark.anyio
    async def test_platform_admin_invite_flags_row(
        self, session: AsyncSession, org: Organization, admin: User
    ):
        """A superuser-created invitation is flagged as platform-created."""
        superuser_role = Role(
            type="user",
            user_id=admin.id,
            organization_id=org.id,
            service_id="tracecat-api",
            is_platform_superuser=True,
            scopes=ORG_ADMIN_SCOPES,
        )
        service = InvitationService(session, role=superuser_role)
        owner_role_id = await _role_id(session, org.id, "organization-owner")

        invitation = await service.create_invitation(
            InvitationCreate(
                email="platform@example.com",
                grants=[InvitationGrant(role_id=owner_role_id)],
            )
        )

        assert invitation.created_by_platform_admin is True


class TestAcceptInvitationGrants:
    """What accepting an invitation assigns."""

    @pytest.mark.anyio
    async def test_multi_grant_accept_assigns_every_grant(
        self,
        session: AsyncSession,
        org: Organization,
        admin: User,
        invitee: User,
        workspace_a: Workspace,
        workspace_b: Workspace,
    ):
        """Accepting yields exactly the grants, org-member added only if absent."""
        service = InvitationService(session, role=_admin_role(org.id, admin.id))
        member_role_id = await _role_id(session, org.id, "organization-member")
        editor_role_id = await _role_id(session, org.id, "workspace-editor")
        admin_role_id = await _role_id(session, org.id, "workspace-admin")

        invitation = await service.create_invitation(
            InvitationCreate(
                email=invitee.email,
                grants=[
                    InvitationGrant(role_id=member_role_id),
                    InvitationGrant(
                        workspace_id=workspace_a.id, role_id=editor_role_id
                    ),
                    InvitationGrant(workspace_id=workspace_b.id, role_id=admin_role_id),
                ],
            )
        )

        await accept_invitation_for_user(
            session, user_id=invitee.id, token=invitation.token
        )

        assert await _assignments(session, invitee.id, org.id) == {
            None: member_role_id,
            workspace_a.id: editor_role_id,
            workspace_b.id: admin_role_id,
        }
        await session.refresh(invitation)
        assert invitation.status == InvitationStatus.ACCEPTED
        assert invitation.accepted_at is not None

    @pytest.mark.anyio
    async def test_workspace_only_grant_adds_org_member(
        self,
        session: AsyncSession,
        org: Organization,
        admin: User,
        invitee: User,
        workspace_a: Workspace,
    ):
        """A workspace-only invite also makes the user an org member."""
        service = InvitationService(session, role=_admin_role(org.id, admin.id))
        editor_role_id = await _role_id(session, org.id, "workspace-editor")
        member_role_id = await _role_id(session, org.id, "organization-member")

        invitation = await service.create_invitation(
            InvitationCreate(
                email=invitee.email,
                grants=[
                    InvitationGrant(workspace_id=workspace_a.id, role_id=editor_role_id)
                ],
            )
        )

        await accept_invitation_for_user(
            session, user_id=invitee.id, token=invitation.token
        )

        assert await _assignments(session, invitee.id, org.id) == {
            None: member_role_id,
            workspace_a.id: editor_role_id,
        }

    @pytest.mark.anyio
    async def test_org_grant_does_not_add_second_org_row(
        self, session: AsyncSession, org: Organization, admin: User, invitee: User
    ):
        """An org-wide grant is the user's only org-wide assignment."""
        service = InvitationService(session, role=_admin_role(org.id, admin.id))
        org_admin_role_id = await _role_id(session, org.id, "organization-admin")

        invitation = await service.create_invitation(
            InvitationCreate(
                email=invitee.email,
                grants=[InvitationGrant(role_id=org_admin_role_id)],
            )
        )

        await accept_invitation_for_user(
            session, user_id=invitee.id, token=invitation.token
        )

        assert await _assignments(session, invitee.id, org.id) == {
            None: org_admin_role_id
        }

    @pytest.mark.anyio
    async def test_existing_org_role_is_never_overwritten(
        self,
        session: AsyncSession,
        org: Organization,
        admin: User,
        invitee: User,
        workspace_a: Workspace,
    ):
        """An org-wide role the user already holds survives acceptance."""
        await grant_org_membership(
            session,
            user_id=invitee.id,
            organization_id=org.id,
            slug="organization-admin",
        )
        await session.commit()
        held_role_id = await _role_id(session, org.id, "organization-admin")
        member_role_id = await _role_id(session, org.id, "organization-member")
        editor_role_id = await _role_id(session, org.id, "workspace-editor")

        # An org member cannot be invited, so the invitation is written directly.
        invitation = Invitation(
            organization_id=org.id,
            email=invitee.email,
            status=InvitationStatus.PENDING,
            token=uuid.uuid4().hex * 2,
            expires_at=datetime.now(UTC) + timedelta(days=7),
        )
        invitation.grants = [
            InvitationGrantRow(organization_id=org.id, role_id=member_role_id),
            InvitationGrantRow(
                organization_id=org.id,
                workspace_id=workspace_a.id,
                role_id=editor_role_id,
            ),
        ]
        session.add(invitation)
        await session.commit()

        await accept_invitation_for_user(
            session, user_id=invitee.id, token=invitation.token
        )

        assignments = await _assignments(session, invitee.id, org.id)
        assert assignments[None] == held_role_id
        assert assignments[workspace_a.id] == editor_role_id

    @pytest.mark.anyio
    async def test_group_only_member_keeps_indirect_grant(
        self,
        session: AsyncSession,
        org: Organization,
        admin: User,
        invitee: User,
        workspace_a: Workspace,
    ):
        """Derived presence through a group blocks a direct org-member row."""
        await grant_org_membership_via_group(
            session, user_id=invitee.id, organization_id=org.id
        )
        await session.commit()
        editor_role_id = await _role_id(session, org.id, "workspace-editor")

        invitation = Invitation(
            organization_id=org.id,
            email=invitee.email,
            status=InvitationStatus.PENDING,
            token=uuid.uuid4().hex * 2,
            expires_at=datetime.now(UTC) + timedelta(days=7),
        )
        invitation.grants = [
            InvitationGrantRow(
                organization_id=org.id,
                workspace_id=workspace_a.id,
                role_id=editor_role_id,
            )
        ]
        session.add(invitation)
        await session.commit()

        await accept_invitation_for_user(
            session, user_id=invitee.id, token=invitation.token
        )

        assignments = await _assignments(session, invitee.id, org.id)
        assert assignments == {workspace_a.id: editor_role_id}


class TestGrantWorkspaceDeletion:
    """Deleting a granted workspace must not delete the invitation."""

    @pytest.mark.anyio
    async def test_granted_workspace_delete_keeps_invitation(
        self,
        session: AsyncSession,
        org: Organization,
        admin: User,
        workspace_a: Workspace,
        workspace_b: Workspace,
    ):
        """The invitation survives; the dead workspace's grant row is gone."""
        service = InvitationService(session, role=_admin_role(org.id, admin.id))
        editor_role_id = await _role_id(session, org.id, "workspace-editor")

        invitation = await service.create_invitation(
            InvitationCreate(
                email="anchor@example.com",
                grants=[
                    InvitationGrant(
                        workspace_id=workspace_a.id, role_id=editor_role_id
                    ),
                    InvitationGrant(
                        workspace_id=workspace_b.id, role_id=editor_role_id
                    ),
                ],
            )
        )
        invitation_id = invitation.id
        workspace_b_id = workspace_b.id

        await session.execute(delete(Workspace).where(Workspace.id == workspace_a.id))
        await session.commit()
        session.expire_all()

        # The grant cascades with its workspace; the invitation itself survives.
        assert (
            await session.execute(
                select(Invitation.id).where(Invitation.id == invitation_id)
            )
        ).scalar_one() == invitation_id
        stored = (
            (
                await session.execute(
                    select(InvitationGrantRow.workspace_id).where(
                        InvitationGrantRow.invitation_id == invitation_id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert list(stored) == [workspace_b_id]


class TestGrantlessInvitation:
    """A workspace-only invitation whose last workspace was deleted is unacceptable."""

    @pytest.mark.anyio
    async def test_grantless_invitation_hidden_from_members_and_unacceptable(
        self,
        session: AsyncSession,
        org: Organization,
        admin: User,
        workspace_a: Workspace,
    ):
        """The listing drops it and accepting raises instead of 500ing."""
        service = InvitationService(session, role=_admin_role(org.id, admin.id))
        editor_role_id = await _role_id(session, org.id, "workspace-editor")

        invitation = await service.create_invitation(
            InvitationCreate(
                email="grantless@example.com",
                grants=[
                    InvitationGrant(workspace_id=workspace_a.id, role_id=editor_role_id)
                ],
            )
        )
        token = invitation.token
        invitation_id = invitation.id

        invitee = User(
            id=uuid.uuid4(),
            email="grantless@example.com",
            hashed_password="hashed",
            role=UserRole.BASIC,
            is_active=True,
            is_superuser=False,
            is_verified=True,
        )
        session.add(invitee)
        await session.commit()

        await session.execute(delete(Workspace).where(Workspace.id == workspace_a.id))
        await session.commit()
        session.expunge_all()

        # The only grant cascaded away, leaving nothing to confer.
        assert (
            await session.execute(
                select(func.count())
                .select_from(InvitationGrantRow)
                .where(InvitationGrantRow.invitation_id == invitation_id)
            )
        ).scalar_one() == 0

        # A grantless invitation confers nothing, so it is not listed as a member.
        members = await list_org_members(
            role=_admin_role(org.id, admin.id), session=session
        )
        assert [m for m in members if m.email == "grantless@example.com"] == []

        with pytest.raises(TracecatAuthorizationError):
            await accept_invitation_for_user(session, user_id=invitee.id, token=token)

        # The admin listing drops it as well.
        listed = await service.list_invitations(status=InvitationStatus.PENDING)
        assert invitation_id not in [inv.id for inv in listed]

        # SAML and pending-for-me lookups skip it too.
        assert (
            await get_pending_invitation_for_email(
                session, organization_id=org.id, email="grantless@example.com"
            )
            is None
        )

        # It no longer blocks a replacement invitation for the same email.
        admin_role_id = await _role_id(session, org.id, "organization-admin")
        replacement = await service.create_invitation(
            InvitationCreate(
                email="grantless@example.com",
                grants=[InvitationGrant(role_id=admin_role_id)],
            )
        )
        assert replacement.id != invitation_id
        assert len(replacement.grants) == 1


class TestGrantRoleDeletion:
    """Deleting a granted role must retire the grant, not the invitation."""

    @pytest.mark.anyio
    async def test_deleted_role_cascades_grant_and_blocks_accept(
        self,
        session: AsyncSession,
        org: Organization,
        admin: User,
        workspace_a: Workspace,
    ):
        """The grant row goes with its role, leaving the invitation unacceptable."""
        service = InvitationService(session, role=_admin_role(org.id, admin.id))
        custom_role = DBRole(
            id=uuid.uuid4(),
            name="Temp Workspace Role",
            slug=None,
            organization_id=org.id,
        )
        session.add(custom_role)
        await session.commit()

        invitation = await service.create_invitation(
            InvitationCreate(
                email="roleless@example.com",
                grants=[
                    InvitationGrant(workspace_id=workspace_a.id, role_id=custom_role.id)
                ],
            )
        )
        token = invitation.token
        invitation_id = invitation.id

        invitee = User(
            id=uuid.uuid4(),
            email="roleless@example.com",
            hashed_password="hashed",
            role=UserRole.BASIC,
            is_active=True,
            is_superuser=False,
            is_verified=True,
        )
        session.add(invitee)
        await session.commit()

        await session.execute(delete(DBRole).where(DBRole.id == custom_role.id))
        await session.commit()
        session.expunge_all()

        assert (
            await session.execute(
                select(Invitation.id).where(Invitation.id == invitation_id)
            )
        ).scalar_one() == invitation_id
        assert (
            await session.execute(
                select(func.count())
                .select_from(InvitationGrantRow)
                .where(InvitationGrantRow.invitation_id == invitation_id)
            )
        ).scalar_one() == 0

        with pytest.raises(TracecatAuthorizationError):
            await accept_invitation_for_user(session, user_id=invitee.id, token=token)


class TestDuplicateGrantScopes:
    """One grant per scope; the organization counts as a scope."""

    def test_duplicate_workspace_scope_rejected(self):
        workspace_id = uuid.uuid4()
        role_id = uuid.uuid4()
        with pytest.raises(ValidationError, match="at most once in grants"):
            InvitationCreate(
                email="dupe@example.com",
                grants=[
                    InvitationGrant(workspace_id=workspace_id, role_id=role_id),
                    InvitationGrant(workspace_id=workspace_id, role_id=role_id),
                ],
            )

    def test_duplicate_org_scope_rejected(self):
        role_id = uuid.uuid4()
        with pytest.raises(ValidationError, match="at most once in grants"):
            InvitationCreate(
                email="dupe@example.com",
                grants=[
                    InvitationGrant(role_id=role_id),
                    InvitationGrant(role_id=role_id),
                ],
            )

    def test_distinct_scopes_accepted(self):
        role_id = uuid.uuid4()
        params = InvitationCreate(
            email="ok@example.com",
            grants=[
                InvitationGrant(role_id=role_id),
                InvitationGrant(workspace_id=uuid.uuid4(), role_id=role_id),
            ],
        )
        assert len(params.grants) == 2
