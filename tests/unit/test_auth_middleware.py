"""Tests for the authorization cache middleware."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from tests.support.membership import grant_org_membership, grant_workspace_membership
from tracecat import config
from tracecat.auth.credentials import (
    _authenticate_user,
    _role_dependency,
    authenticated_user_only,
)
from tracecat.auth.org_context import resolve_auth_organization_id
from tracecat.auth.schemas import UserRole
from tracecat.auth.types import Role
from tracecat.contexts import ctx_agent_session_id, ctx_role
from tracecat.db.models import (
    Organization,
    User,
    Workspace,
)
from tracecat.organization.management import SingleTenantUserDefaultsResult


@pytest.mark.anyio
async def test_authenticated_user_only_does_not_activate_superuser_privileges() -> None:
    """User.is_superuser is eligibility; generic auth must not activate it."""
    user = MagicMock(spec=User)
    user.id = uuid.uuid4()
    user.is_superuser = True

    async def _compute_effective_scopes(role: Role) -> frozenset[str]:
        assert role.is_platform_superuser is False
        return frozenset()

    role_token = ctx_role.set(None)
    agent_session_token = ctx_agent_session_id.set(uuid.uuid4())
    try:
        with patch(
            "tracecat.auth.credentials.compute_effective_scopes",
            new=AsyncMock(side_effect=_compute_effective_scopes),
        ) as mock_compute_scopes:
            role = await authenticated_user_only(user=user)

        assert role.user_id == user.id
        assert role.organization_id is None
        assert role.workspace_id is None
        assert role.is_platform_superuser is False
        assert role.scopes == frozenset()
        assert ctx_role.get() == role
        assert ctx_agent_session_id.get() is None
        mock_compute_scopes.assert_awaited_once()
    finally:
        ctx_agent_session_id.reset(agent_session_token)
        ctx_role.reset(role_token)


@pytest.mark.anyio
async def test_role_dependency_rebinds_rls_context_on_session(
    monkeypatch: pytest.MonkeyPatch,
):
    """Role resolution should re-apply RLS context on the request session."""
    monkeypatch.setattr(config, "TRACECAT__RLS_MODE", config.RLSMode.ENFORCE)

    workspace_id = uuid.uuid4()
    org_id = uuid.uuid4()
    user_id = uuid.uuid4()
    request = MagicMock(spec=Request)
    request.state = MagicMock()
    session = AsyncMock()
    user = MagicMock(spec=User)

    role = Role(
        type="user",
        workspace_id=workspace_id,
        organization_id=org_id,
        user_id=user_id,
        service_id="tracecat-api",
    )
    validated_role = role.model_copy(update={"scopes": frozenset({"tests:read"})})

    with (
        patch(
            "tracecat.auth.credentials._authenticate_user",
            new=AsyncMock(return_value=role),
        ),
        patch(
            "tracecat.auth.credentials._validate_role",
            new=AsyncMock(return_value=validated_role),
        ),
        patch(
            "tracecat.auth.credentials.set_rls_context_from_role",
            new=AsyncMock(),
        ) as mock_set_rls,
    ):
        result = await _role_dependency(
            request=request,
            session=session,
            workspace_id=workspace_id,
            user=user,
            api_key=None,
            allow_user=True,
            allow_service=False,
            allow_executor=False,
            require_workspace="yes",
        )

    assert result == validated_role
    mock_set_rls.assert_awaited_once_with(session, validated_role)


@pytest.mark.anyio
async def test_user_role_dependency_clears_agent_session_context() -> None:
    workspace_id = uuid.uuid4()
    user = MagicMock(spec=User)
    role = Role(
        type="user",
        workspace_id=workspace_id,
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        service_id="tracecat-api",
    )
    context_token = ctx_agent_session_id.set(uuid.uuid4())
    try:
        with (
            patch("tracecat.auth.credentials.set_rls_context", new=AsyncMock()),
            patch(
                "tracecat.auth.credentials._authenticate_user",
                new=AsyncMock(return_value=role),
            ),
            patch(
                "tracecat.auth.credentials._validate_role",
                new=AsyncMock(return_value=role),
            ),
        ):
            await _role_dependency(
                request=MagicMock(spec=Request),
                session=AsyncMock(),
                workspace_id=workspace_id,
                user=user,
                api_key=None,
                allow_user=True,
                allow_service=False,
                allow_executor=False,
                require_workspace="yes",
            )

        assert ctx_agent_session_id.get() is None
    finally:
        ctx_agent_session_id.reset(context_token)


@pytest.mark.anyio
async def test_role_dependency_preserves_auth_exception_when_cleanup_fails():
    """Cleanup errors must not mask the original auth exception."""
    workspace_id = uuid.uuid4()
    request = MagicMock(spec=Request)
    request.state = MagicMock()
    session = AsyncMock()
    user = MagicMock(spec=User)

    original_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="auth failure",
    )

    with (
        patch(
            "tracecat.auth.credentials.set_rls_context",
            new=AsyncMock(),
        ),
        patch(
            "tracecat.auth.credentials._authenticate_user",
            new=AsyncMock(side_effect=original_exc),
        ),
        patch(
            "tracecat.auth.credentials.set_rls_context_from_role",
            new=AsyncMock(side_effect=RuntimeError("cleanup failed")),
        ) as mock_cleanup,
    ):
        with pytest.raises(HTTPException) as excinfo:
            await _role_dependency(
                request=request,
                session=session,
                workspace_id=workspace_id,
                user=user,
                api_key=None,
                allow_user=True,
                allow_service=False,
                allow_executor=False,
                require_workspace="yes",
            )

    assert excinfo.value is original_exc
    mock_cleanup.assert_awaited_once_with(session, None)


@pytest.mark.anyio
async def test_role_dependency_resolves_multi_tenant_superuser_as_regular_org_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A superuser flag alone must not grant platform privileges in tenant context."""
    monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", True)

    request = MagicMock(spec=Request)
    request.state = MagicMock()
    request.cookies = {"tracecat-org-id": str(uuid.uuid4())}
    session = AsyncMock()
    user = MagicMock(spec=User)
    user.id = uuid.uuid4()
    user.is_superuser = True
    org_id = uuid.uuid4()
    scopes = frozenset({"org:read"})

    with (
        patch(
            "tracecat.auth.credentials._resolve_org_for_regular_user",
            new=AsyncMock(return_value=org_id),
        ) as mock_resolve_org,
        patch(
            "tracecat.auth.credentials.compute_effective_scopes",
            new=AsyncMock(return_value=scopes),
        ),
        patch("tracecat.auth.credentials.set_rls_context", new=AsyncMock()),
        patch(
            "tracecat.auth.credentials.set_rls_context_from_role",
            new=AsyncMock(),
        ),
    ):
        role = await _role_dependency(
            request=request,
            session=session,
            workspace_id=None,
            user=user,
            api_key=None,
            allow_user=True,
            allow_service=False,
            allow_executor=False,
            require_workspace="no",
        )

    assert role.organization_id == org_id
    assert role.user_id == user.id
    assert role.is_platform_superuser is False
    assert role.scopes == scopes
    mock_resolve_org.assert_awaited_once_with(request, session, user)


@pytest.mark.anyio
async def test_authenticate_user_only_invalidates_scope_cache_when_defaults_change() -> (
    None
):
    request = MagicMock(spec=Request)
    request.state = MagicMock()
    user = MagicMock(spec=User)
    user.id = uuid.uuid4()
    user.is_superuser = False
    org_id = uuid.uuid4()

    for changed, expected_invalidations in ((False, 0), (True, 1)):
        session = AsyncMock()
        with (
            patch(
                "tracecat.auth.credentials.ensure_single_tenant_user_defaults_for_session",
                new=AsyncMock(
                    return_value=SingleTenantUserDefaultsResult(
                        organization_id=org_id,
                        changed=changed,
                    )
                ),
            ),
            patch(
                "tracecat.auth.credentials.set_rls_context",
                new=AsyncMock(),
            ) as mock_set_rls,
            patch(
                "tracecat.auth.credentials._invalidate_user_scope_cache"
            ) as mock_invalidate,
        ):
            role = await _authenticate_user(
                request=request,
                session=session,
                user=user,
                workspace_id=None,
            )

        assert role.organization_id == org_id
        assert mock_invalidate.call_count == expected_invalidations
        if changed:
            session.commit.assert_awaited_once()
            mock_set_rls.assert_awaited_once()
        else:
            session.commit.assert_not_awaited()
            mock_set_rls.assert_not_awaited()


@pytest.mark.anyio
async def test_authenticate_user_does_not_enroll() -> None:
    """The auth path leaves enrollment to provisioning or invitation."""
    request = MagicMock(spec=Request)
    request.state = MagicMock()
    user = MagicMock(spec=User)
    user.id = uuid.uuid4()
    user.is_superuser = False
    session = AsyncMock()

    defaults = AsyncMock(
        return_value=SingleTenantUserDefaultsResult(
            organization_id=None,
            changed=False,
        )
    )
    with (
        patch(
            "tracecat.auth.credentials.ensure_single_tenant_user_defaults_for_session",
            new=defaults,
        ),
        patch(
            "tracecat.auth.credentials._resolve_org_for_regular_user",
            new=AsyncMock(return_value=uuid.uuid4()),
        ),
        patch(
            "tracecat.auth.credentials.compute_effective_scopes",
            new=AsyncMock(return_value=frozenset()),
        ),
        patch("tracecat.auth.credentials.set_rls_context", new=AsyncMock()),
    ):
        await _authenticate_user(
            request=request,
            session=session,
            user=user,
            workspace_id=None,
        )

    assert defaults.await_args is not None
    assert defaults.await_args.kwargs.get("allow_new_members", False) is False


@pytest.mark.anyio
async def test_role_dependency_resolves_superuser_workspace_membership_without_platform_scopes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Workspace access for superuser accounts still depends on membership/RBAC."""
    monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", True)

    request = MagicMock(spec=Request)
    request.state = MagicMock()
    request.cookies = {}
    session = AsyncMock()
    workspace_id = uuid.uuid4()
    user = MagicMock(spec=User)
    user.id = uuid.uuid4()
    user.is_superuser = True
    org_id = uuid.uuid4()
    scopes = frozenset({"workspace:read"})
    with (
        patch(
            "tracecat.auth.credentials._get_workspace_org_id",
            new=AsyncMock(return_value=org_id),
        ),
        patch(
            "tracecat.auth.credentials._is_org_admin_via_rbac",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "tracecat.auth.credentials._require_workspace_membership",
            new=AsyncMock(return_value=None),
        ) as mock_get_membership,
        patch(
            "tracecat.auth.credentials.compute_effective_scopes",
            new=AsyncMock(return_value=scopes),
        ),
        patch("tracecat.auth.credentials.set_rls_context", new=AsyncMock()),
        patch(
            "tracecat.auth.credentials.set_rls_context_from_role",
            new=AsyncMock(),
        ),
    ):
        role = await _role_dependency(
            request=request,
            session=session,
            workspace_id=workspace_id,
            user=user,
            api_key=None,
            allow_user=True,
            allow_service=False,
            allow_executor=False,
            require_workspace="yes",
        )

    assert role.organization_id == org_id
    assert role.workspace_id == workspace_id
    assert role.user_id == user.id
    assert role.is_platform_superuser is False
    assert role.scopes == scopes
    mock_get_membership.assert_awaited_once_with(
        session=session,
        workspace_id=workspace_id,
        user=user,
    )


@pytest.mark.anyio
async def test_resolve_auth_organization_id_ignores_org_cookie_in_multi_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-auth multi-tenant org resolution requires explicit org links."""
    monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", True)

    request = MagicMock(spec=Request)
    request.query_params = {}
    request.cookies = {"tracecat-org-id": str(uuid.uuid4())}
    session = AsyncMock(spec=AsyncSession)

    with pytest.raises(HTTPException) as excinfo:
        await resolve_auth_organization_id(request, session=session)

    assert excinfo.value.status_code == status.HTTP_428_PRECONDITION_REQUIRED
    assert excinfo.value.detail == "Organization selection required"
    session.execute.assert_not_called()


@pytest.mark.anyio
async def test_organization_id_populated_when_require_workspace_no(
    mocker, monkeypatch: pytest.MonkeyPatch
):
    """Test that organization_id is inferred from membership when require_workspace="no"."""

    monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", True)

    # Create mock user (non-superuser to exercise org membership resolution)
    mock_user = MagicMock(spec=User)
    mock_user.id = uuid.uuid4()
    mock_user.role = UserRole.ADMIN
    mock_user.is_superuser = False

    # Create a mock organization for the user to belong to
    test_org_id = uuid.uuid4()

    # Mock session - need to properly mock execute() for org membership lookup
    # The code does: org_ids = org_membership_result.scalars().all()
    mock_session = AsyncMock()

    # First call: membership query returns the org_id
    org_result = MagicMock()
    org_result.scalars.return_value.all.return_value = [test_org_id]

    # Second call: membership lookup for org_role returns None
    org_role_result = MagicMock()
    org_role_result.scalar_one_or_none.return_value = None

    # Third call: compute_effective_scopes query returns empty scopes
    scopes_result = MagicMock()
    scopes_result.scalars.return_value.all.return_value = []

    mock_session.execute.side_effect = [org_result, org_role_result, scopes_result]

    mocker.patch(
        "tracecat.auth.credentials.set_rls_context",
        new=AsyncMock(),
    )
    mocker.patch(
        "tracecat.auth.credentials.set_rls_context_from_role",
        new=AsyncMock(),
    )
    request = MagicMock(spec=Request)
    request.state = MagicMock()

    # Test with require_workspace="no" - organization_id should be inferred from membership
    role = await _role_dependency(
        request=request,
        session=mock_session,
        workspace_id=None,  # No workspace ID
        user=mock_user,
        api_key=None,
        allow_user=True,
        allow_service=False,
        require_workspace="no",
    )

    # Verify organization_id was inferred from the user's membership
    assert role.organization_id == test_org_id
    assert role.workspace_id is None
    assert role.user_id == mock_user.id


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_role_dependency_infers_org_from_single_membership(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", True)

    org_id = uuid.uuid4()
    org = Organization(
        id=org_id,
        name="Test Org",
        slug=f"test-org-{org_id.hex[:8]}",
        is_active=True,
    )
    user = User(
        id=uuid.uuid4(),
        email=f"user-{uuid.uuid4()}@example.com",
        hashed_password="test_password",
        is_active=True,
        is_verified=True,
        is_superuser=False,
        last_login_at=None,
        role=UserRole.BASIC,
    )
    workspace = Workspace(
        id=uuid.uuid4(),
        name="Test Workspace",
        organization_id=org.id,
    )
    session.add_all([org, user, workspace])
    await session.commit()

    # Org membership is required for org context resolution.
    await grant_org_membership(session, user_id=user.id, organization_id=org.id)
    await grant_workspace_membership(
        session,
        user_id=user.id,
        organization_id=org.id,
        workspace_id=workspace.id,
    )
    await session.commit()

    request = MagicMock(spec=Request)
    request.state = MagicMock()

    role = await _role_dependency(
        request=request,
        session=session,
        workspace_id=None,
        user=user,
        api_key=None,
        allow_user=True,
        allow_service=False,
        require_workspace="no",
    )

    assert role.organization_id == org.id
    assert role.workspace_id is None
    assert role.user_id == user.id


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
@pytest.mark.parametrize("require_workspace", ["no", "optional"])
async def test_role_dependency_uses_stable_org_for_multi_org_without_workspace(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    require_workspace: Literal["no", "optional"],
):
    monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", True)

    base_time = datetime(2024, 1, 1, tzinfo=UTC)
    org_a_id = uuid.uuid4()
    org_b_id = uuid.uuid4()
    org_a = Organization(
        id=org_a_id,
        name="Org A",
        slug=f"org-a-{org_a_id.hex[:8]}",
        is_active=True,
        created_at=base_time,
    )
    org_b = Organization(
        id=org_b_id,
        name="Org B",
        slug=f"org-b-{org_b_id.hex[:8]}",
        is_active=True,
        created_at=base_time + timedelta(days=1),
    )
    user = User(
        id=uuid.uuid4(),
        email=f"user-{uuid.uuid4()}@example.com",
        hashed_password="test_password",
        is_active=True,
        is_verified=True,
        is_superuser=False,
        last_login_at=None,
        role=UserRole.BASIC,
    )
    workspace_a = Workspace(
        id=uuid.uuid4(),
        name="Workspace A",
        organization_id=org_a.id,
    )
    workspace_b = Workspace(
        id=uuid.uuid4(),
        name="Workspace B",
        organization_id=org_b.id,
    )
    session.add_all([org_a, org_b, user, workspace_a, workspace_b])
    await session.commit()

    for org, workspace in ((org_a, workspace_a), (org_b, workspace_b)):
        await grant_org_membership(session, user_id=user.id, organization_id=org.id)
        await grant_workspace_membership(
            session,
            user_id=user.id,
            organization_id=org.id,
            workspace_id=workspace.id,
        )
    await session.commit()

    request = MagicMock(spec=Request)
    request.state = MagicMock()
    request.cookies = {}

    role = await _role_dependency(
        request=request,
        session=session,
        workspace_id=None,
        user=user,
        api_key=None,
        allow_user=True,
        allow_service=False,
        require_workspace=require_workspace,
    )

    assert role.organization_id == org_a.id
    assert role.workspace_id is None
    assert role.user_id == user.id
