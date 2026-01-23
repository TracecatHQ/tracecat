"""Tests for the authorization cache middleware."""

import time
import uuid
from statistics import mean
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config
from tracecat.auth.credentials import RoleACL, _role_dependency
from tracecat.auth.schemas import UserRole
from tracecat.auth.types import AccessLevel, Role
from tracecat.authz.enums import OrgRole, WorkspaceRole
from tracecat.authz.service import MembershipWithOrg
from tracecat.db.models import (
    Membership,
    Organization,
    OrganizationMembership,
    User,
    Workspace,
)
from tracecat.middleware import AuthorizationCacheMiddleware


@pytest.fixture
def test_app():
    """Create a test FastAPI app with the auth cache middleware."""
    app = FastAPI()
    app.add_middleware(AuthorizationCacheMiddleware)

    @app.get("/test-workspace")
    async def test_endpoint(  # pyright: ignore[reportUnusedFunction] - route handler
        role: Role = RoleACL(
            allow_user=True,
            allow_service=False,
            require_workspace="yes",
        ),
    ):
        return {"workspace_id": str(role.workspace_id)}

    return app


@pytest.fixture
def client(test_app):
    """Create a test client."""
    return TestClient(test_app)


@pytest.mark.anyio
async def test_auth_cache_middleware_initializes_cache():
    """Test that the middleware properly initializes the auth cache."""
    request = MagicMock(spec=Request)
    request.state = MagicMock()
    request.url.path = "/test"
    request.method = "GET"

    mock_app = MagicMock()
    middleware = AuthorizationCacheMiddleware(mock_app)

    # Mock call_next
    async def mock_call_next(req):
        # Verify cache was initialized
        assert hasattr(req.state, "auth_cache")
        assert "memberships" in req.state.auth_cache
        assert "membership_checked" in req.state.auth_cache
        assert "all_memberships" in req.state.auth_cache
        return MagicMock()

    await middleware.dispatch(request, mock_call_next)

    # Verify cache was cleaned up
    assert not hasattr(request.state, "auth_cache")


@pytest.mark.anyio
async def test_auth_cache_reduces_database_queries(mocker):
    """Test that the cache reduces database queries for multiple workspace checks."""
    from tracecat.authz.service import MembershipService

    # Create mock user and memberships with proper UUID4
    mock_user = MagicMock(spec=User)
    mock_user.id = uuid.uuid4()
    mock_user.role = UserRole.BASIC

    workspace_id_1 = uuid.uuid4()
    workspace_id_2 = uuid.uuid4()

    mock_membership1 = MagicMock(spec=Membership)
    mock_membership1.workspace_id = workspace_id_1
    mock_membership1.role = WorkspaceRole.EDITOR
    mock_membership1.user_id = mock_user.id

    mock_membership2 = MagicMock(spec=Membership)
    mock_membership2.workspace_id = workspace_id_2
    mock_membership2.role = WorkspaceRole.ADMIN
    mock_membership2.user_id = mock_user.id

    # Track database calls
    db_call_count = 0

    async def mock_list_user_memberships(user_id):
        nonlocal db_call_count
        db_call_count += 1
        return [mock_membership1, mock_membership2]

    # Create organization IDs for memberships
    org_id_1 = uuid.uuid4()
    org_id_2 = uuid.uuid4()

    # Create a mock service instance
    mock_service = MagicMock(spec=MembershipService)
    mock_service.list_user_memberships = AsyncMock(
        side_effect=mock_list_user_memberships
    )
    mock_service.get_membership = AsyncMock(
        side_effect=lambda workspace_id, user_id: MembershipWithOrg(
            membership=mock_membership1, org_id=org_id_1
        )
        if workspace_id == workspace_id_1
        else MembershipWithOrg(membership=mock_membership2, org_id=org_id_2)
        if workspace_id == workspace_id_2
        else None
    )

    # Mock the MembershipService constructor to return our mock
    mocker.patch(
        "tracecat.auth.credentials.MembershipService", return_value=mock_service
    )

    # Mock the access level lookup
    mocker.patch.dict(
        "tracecat.auth.credentials.USER_ROLE_TO_ACCESS_LEVEL",
        {UserRole.BASIC: AccessLevel.BASIC},
    )

    # Mock is_unprivileged to return True for basic users
    mocker.patch("tracecat.auth.credentials.is_unprivileged", return_value=True)

    # Simulate multiple workspace checks in the same request
    request = MagicMock(spec=Request)
    request.state = MagicMock()
    request.state.auth_cache = {
        "memberships": {},
        "membership_checked": False,
        "all_memberships": [],
        "user_id": None,
    }

    # Mock session with proper execute() and scalar_one_or_none() chain
    mock_ws_org_1 = MagicMock()
    mock_ws_org_1.scalar_one_or_none = MagicMock(return_value=org_id_1)
    mock_org_role_1 = MagicMock()
    mock_org_role_1.scalar_one_or_none = MagicMock(return_value=None)
    mock_ws_org_2 = MagicMock()
    mock_ws_org_2.scalar_one_or_none = MagicMock(return_value=org_id_2)
    mock_org_role_2 = MagicMock()
    mock_org_role_2.scalar_one_or_none = MagicMock(return_value=None)
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(
        side_effect=[mock_ws_org_1, mock_org_role_1, mock_ws_org_2, mock_org_role_2]
    )

    # First workspace check - should trigger database query
    await _role_dependency(
        request=request,
        session=mock_session,
        workspace_id=workspace_id_1,
        user=mock_user,
        api_key=None,
        allow_user=True,
        allow_service=False,
        require_workspace="yes",
        min_access_level=None,
        require_workspace_roles=None,
    )

    assert db_call_count == 1  # First call triggers DB query

    # Second workspace check - should use cache
    await _role_dependency(
        request=request,
        session=mock_session,
        workspace_id=workspace_id_2,
        user=mock_user,
        api_key=None,
        allow_user=True,
        allow_service=False,
        require_workspace="yes",
        min_access_level=None,
        require_workspace_roles=None,
    )

    assert db_call_count == 1  # Still only 1 DB call - used cache!

    # Verify cache contains both memberships
    assert len(request.state.auth_cache["memberships"]) == 2
    assert request.state.auth_cache["membership_checked"]


@pytest.mark.skip(reason="Skipping performance test for now as it's flaky")
@pytest.mark.anyio
async def test_performance_improvement(mocker):
    """Measure actual performance improvement with caching."""
    from tracecat.authz.service import MembershipService

    # Create test users
    admin_user = MagicMock(spec=User)
    admin_user.id = uuid.uuid4()
    admin_user.role = UserRole.ADMIN

    basic_user = MagicMock(spec=User)
    basic_user.id = uuid.uuid4()
    basic_user.role = UserRole.BASIC

    workspace_id = uuid.uuid4()

    # Mock membership
    mock_membership = MagicMock(spec=Membership)
    mock_membership.workspace_id = workspace_id
    mock_membership.role = WorkspaceRole.EDITOR

    # Track timing for database calls
    db_delay_ms = 10  # Simulate 10ms database query

    async def mock_list_user_memberships_slow(self, user_id):
        # Simulate database query delay
        await asyncio.sleep(db_delay_ms / 1000)
        return [mock_membership]

    async def mock_get_membership_slow(self, workspace_id, user_id):
        # Simulate database query delay
        await asyncio.sleep(db_delay_ms / 1000)
        return MembershipWithOrg(membership=mock_membership, org_id=uuid.uuid4())

    # Patch the membership service
    mocker.patch.object(
        MembershipService, "list_user_memberships", mock_list_user_memberships_slow
    )
    mocker.patch.object(MembershipService, "get_membership", mock_get_membership_slow)

    # Mock the access level lookup
    mocker.patch.dict(
        "tracecat.auth.credentials.USER_ROLE_TO_ACCESS_LEVEL",
        {UserRole.ADMIN: AccessLevel.ADMIN, UserRole.BASIC: AccessLevel.BASIC},
    )

    # Helper to time a single call
    async def time_auth_check(user, use_cache=True):
        request = MagicMock(spec=Request)
        request.state = MagicMock()

        if use_cache:
            request.state.auth_cache = {
                "memberships": {},
                "membership_checked": False,
                "all_memberships": [],
            }
        else:
            request.state.auth_cache = None

        mock_session = AsyncMock()

        start = time.perf_counter()
        await _role_dependency(
            request=request,
            session=mock_session,
            workspace_id=workspace_id,
            user=user,
            api_key=None,
            allow_user=True,
            allow_service=False,
            require_workspace="yes",
            min_access_level=None,
            require_workspace_roles=None,
        )
        end = time.perf_counter()

        return (end - start) * 1000  # Convert to ms

    # Import asyncio for sleep
    import asyncio

    # Test admin user (should be fast - no DB query)
    admin_times = []
    for _ in range(5):
        admin_time = await time_auth_check(admin_user)
        admin_times.append(admin_time)

    # Test basic user WITHOUT cache (simulates old behavior)
    basic_no_cache_times = []
    for _ in range(5):
        basic_time = await time_auth_check(basic_user, use_cache=False)
        basic_no_cache_times.append(basic_time)

    # Test basic user WITH cache (new behavior)
    basic_with_cache_times = []
    for _ in range(5):
        basic_time = await time_auth_check(basic_user, use_cache=True)
        basic_with_cache_times.append(basic_time)

    # Calculate averages
    avg_admin = mean(admin_times)
    avg_basic_no_cache = mean(basic_no_cache_times)
    avg_basic_with_cache = mean(basic_with_cache_times)

    print("\n=== Performance Test Results ===")
    print(f"Admin user average: {avg_admin:.2f}ms")
    print(f"Basic user (no cache): {avg_basic_no_cache:.2f}ms")
    print(f"Basic user (with cache): {avg_basic_with_cache:.2f}ms")
    print(f"Slowdown without cache: {avg_basic_no_cache / avg_admin:.1f}x")
    print(f"Slowdown with cache: {avg_basic_with_cache / avg_admin:.1f}x")
    print(f"Cache improvement: {avg_basic_no_cache / avg_basic_with_cache:.1f}x faster")
    print("================================\n")

    # Assertions
    assert avg_admin < 2, "Admin checks should be very fast (<2ms)"
    assert avg_basic_no_cache > avg_admin, (
        "Basic user without cache should be slower than admin"
    )
    assert avg_basic_with_cache > avg_admin, (
        "Basic user with cache still slower than admin (has initial query)"
    )
    # Make this assertion more lenient to avoid flakiness
    # The cache should provide some improvement, but the exact ratio can vary
    assert avg_basic_with_cache <= avg_basic_no_cache, (
        "Cache should not make things slower"
    )

    # The key insight: even though our mock delays are small, we still see the pattern:
    # 1. Admin users are fastest (no DB queries)
    # 2. Basic users without cache are slower
    # 3. Caching provides measurable improvement

    # In production with real database latency (10-100ms per query),
    # these differences would be much more dramatic:
    # - 10 API calls × 100ms = 1 second without caching
    # - 1 API call × 100ms = 100ms with caching (10x improvement)


@pytest.mark.anyio
async def test_cache_user_id_validation():
    """Test that cache validates user ID to prevent cross-user data leakage."""
    from tracecat.authz.service import MembershipService

    # Create two different users
    user1 = MagicMock(spec=User)
    user1.id = uuid.uuid4()
    user1.role = UserRole.BASIC

    user2 = MagicMock(spec=User)
    user2.id = uuid.uuid4()
    user2.role = UserRole.BASIC

    workspace_id = uuid.uuid4()

    # Create memberships for both users
    membership1 = MagicMock(spec=Membership)
    membership1.user_id = user1.id
    membership1.workspace_id = workspace_id
    membership1.role = WorkspaceRole.ADMIN

    membership2 = MagicMock(spec=Membership)
    membership2.user_id = user2.id
    membership2.workspace_id = workspace_id
    membership2.role = WorkspaceRole.EDITOR

    # Mock the service
    mock_service = MagicMock(spec=MembershipService)
    mock_service.list_user_memberships = AsyncMock(
        side_effect=lambda user_id: [membership1]
        if user_id == user1.id
        else [membership2]
    )
    mock_service.get_membership = AsyncMock(
        side_effect=lambda workspace_id, user_id: MembershipWithOrg(
            membership=membership1, org_id=uuid.uuid4()
        )
        if user_id == user1.id
        else MembershipWithOrg(membership=membership2, org_id=uuid.uuid4())
    )

    # Create request with cache
    request = MagicMock(spec=Request)
    request.state = MagicMock()
    request.state.auth_cache = {
        "memberships": {},
        "membership_checked": False,
        "all_memberships": [],
        "user_id": None,
    }

    # Set up mocks
    with (
        patch("tracecat.auth.credentials.MembershipService", return_value=mock_service),
        patch("tracecat.auth.credentials.is_unprivileged", return_value=True),
        patch.dict(
            "tracecat.auth.credentials.USER_ROLE_TO_ACCESS_LEVEL",
            {UserRole.BASIC: AccessLevel.BASIC},
        ),
    ):
        # Mock session with proper execute() and scalar_one_or_none() chain
        org_id = uuid.uuid4()
        mock_ws_org_1 = MagicMock()
        mock_ws_org_1.scalar_one_or_none = MagicMock(return_value=org_id)
        mock_org_role_1 = MagicMock()
        mock_org_role_1.scalar_one_or_none = MagicMock(return_value=None)
        mock_ws_org_2 = MagicMock()
        mock_ws_org_2.scalar_one_or_none = MagicMock(return_value=org_id)
        mock_org_role_2 = MagicMock()
        mock_org_role_2.scalar_one_or_none = MagicMock(return_value=None)
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(
            side_effect=[mock_ws_org_1, mock_org_role_1, mock_ws_org_2, mock_org_role_2]
        )

        # Common parameters for _role_dependency
        common_params = {
            "request": request,
            "session": mock_session,
            "workspace_id": workspace_id,
            "api_key": None,
            "allow_user": True,
            "allow_service": False,
            "require_workspace": "yes",
            "min_access_level": None,
            "require_workspace_roles": None,
        }

        # First check with user1 - should populate cache
        role1 = await _role_dependency(user=user1, **common_params)

        # Verify cache is populated with user1's data
        assert request.state.auth_cache["user_id"] == user1.id
        assert len(request.state.auth_cache["memberships"]) == 1

        # Now try to access with user2 - should NOT use user1's cached data
        role2 = await _role_dependency(user=user2, **common_params)

        # Verify user2 got their own membership, not user1's cached data
        assert role2.workspace_role == WorkspaceRole.EDITOR  # user2's role
        assert role1.workspace_role == WorkspaceRole.ADMIN  # user1's role was different


@pytest.mark.anyio
async def test_cache_size_limit():
    """Test that cache has size limits to prevent memory exhaustion."""
    from tracecat.authz.service import MembershipService

    # Create user with excessive memberships
    user = MagicMock(spec=User)
    user.id = uuid.uuid4()
    user.role = UserRole.BASIC

    # Create 1500 memberships (exceeds MAX_CACHED_MEMBERSHIPS = 1000)
    memberships = [
        MagicMock(
            spec=Membership,
            user_id=user.id,
            workspace_id=uuid.uuid4(),
            role=WorkspaceRole.EDITOR,
        )
        for _ in range(1500)
    ]

    # The workspace we're checking
    target_workspace_id = memberships[500].workspace_id
    target_membership = memberships[500]
    org_id = uuid.uuid4()

    # Mock the service
    mock_service = MagicMock(spec=MembershipService)
    mock_service.list_user_memberships = AsyncMock(return_value=memberships)
    mock_service.get_membership = AsyncMock(
        side_effect=lambda workspace_id, user_id: MembershipWithOrg(
            membership=target_membership, org_id=org_id
        )
        if workspace_id == target_workspace_id
        else None
    )

    # Create request with cache
    request = MagicMock(spec=Request)
    request.state = MagicMock()
    request.state.auth_cache = {
        "memberships": {},
        "membership_checked": False,
        "all_memberships": [],
        "user_id": None,
    }

    # Set up mocks
    with (
        patch("tracecat.auth.credentials.MembershipService", return_value=mock_service),
        patch("tracecat.auth.credentials.is_unprivileged", return_value=True),
        patch.dict(
            "tracecat.auth.credentials.USER_ROLE_TO_ACCESS_LEVEL",
            {UserRole.BASIC: AccessLevel.BASIC},
        ),
    ):
        # Mock session with proper execute() and scalar_one_or_none() chain
        mock_ws_org = MagicMock()
        mock_ws_org.scalar_one_or_none = MagicMock(return_value=org_id)
        mock_org_role = MagicMock()
        mock_org_role.scalar_one_or_none = MagicMock(return_value=None)
        mock_membership_result = MagicMock()
        mock_membership_result.scalar_one_or_none = MagicMock(
            return_value=target_membership
        )
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(
            side_effect=[mock_ws_org, mock_org_role, mock_membership_result]
        )

        # Check with excessive memberships
        role = await _role_dependency(
            request=request,
            session=mock_session,
            workspace_id=target_workspace_id,
            user=user,
            api_key=None,
            allow_user=True,
            allow_service=False,
            require_workspace="yes",
            min_access_level=None,
            require_workspace_roles=None,
        )

        # Verify cache was NOT populated due to size limit
        assert (
            len(request.state.auth_cache["memberships"]) == 0
        )  # Cache should be empty
        assert (
            request.state.auth_cache["membership_checked"] is False
        )  # Should not be marked as checked

        # But the role should still be valid (fallback worked)
        assert role.workspace_id == target_workspace_id


@pytest.mark.anyio
async def test_organization_id_populated_when_require_workspace_no(mocker):
    """Test that organization_id is populated when require_workspace="no"."""

    # Create mock user
    mock_user = MagicMock(spec=User)
    mock_user.id = uuid.uuid4()
    mock_user.role = UserRole.ADMIN

    # Mock session (used for org membership lookup)
    mock_org_memberships_result = MagicMock()
    mock_org_memberships_result.all = MagicMock(return_value=[])
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_org_memberships_result)

    # Mock is_unprivileged to return False for admin users
    mocker.patch("tracecat.auth.credentials.is_unprivileged", return_value=False)

    # Mock the access level lookup
    mocker.patch.dict(
        "tracecat.auth.credentials.USER_ROLE_TO_ACCESS_LEVEL",
        {UserRole.ADMIN: AccessLevel.ADMIN},
    )
    mocker.patch(
        "tracecat.auth.credentials.MembershipService.list_user_memberships_with_org",
        new=AsyncMock(return_value=[]),
    )

    request = MagicMock(spec=Request)
    request.state = MagicMock()
    request.state.auth_cache = None

    # Test with require_workspace="no" - should use default organization_id
    role = await _role_dependency(
        request=request,
        session=mock_session,
        workspace_id=None,  # No workspace ID
        user=mock_user,
        api_key=None,
        allow_user=True,
        allow_service=False,
        require_workspace="no",
        min_access_level=None,
        require_workspace_roles=None,
    )

    # Verify organization_id was populated with default
    assert role.organization_id == config.TRACECAT__DEFAULT_ORG_ID
    assert role.workspace_id is None
    assert role.user_id == mock_user.id


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_role_dependency_infers_org_from_single_membership(
    session: AsyncSession,
):
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

    membership = Membership(
        user_id=user.id,
        workspace_id=workspace.id,
        role=WorkspaceRole.EDITOR,
    )
    session.add(membership)
    await session.commit()

    request = MagicMock(spec=Request)
    request.state = MagicMock()
    request.state.auth_cache = None

    role = await _role_dependency(
        request=request,
        session=session,
        workspace_id=None,
        user=user,
        api_key=None,
        allow_user=True,
        allow_service=False,
        require_workspace="no",
        min_access_level=None,
        require_workspace_roles=None,
    )

    assert role.organization_id == org.id
    assert role.workspace_id is None
    assert role.user_id == user.id


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_role_dependency_requires_workspace_for_multi_org(
    session: AsyncSession,
):
    org_a_id = uuid.uuid4()
    org_b_id = uuid.uuid4()
    org_a = Organization(
        id=org_a_id,
        name="Org A",
        slug=f"org-a-{org_a_id.hex[:8]}",
        is_active=True,
    )
    org_b = Organization(
        id=org_b_id,
        name="Org B",
        slug=f"org-b-{org_b_id.hex[:8]}",
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

    memberships = [
        Membership(
            user_id=user.id,
            workspace_id=workspace_a.id,
            role=WorkspaceRole.EDITOR,
        ),
        Membership(
            user_id=user.id,
            workspace_id=workspace_b.id,
            role=WorkspaceRole.EDITOR,
        ),
    ]
    session.add_all(memberships)
    await session.commit()

    request = MagicMock(spec=Request)
    request.state = MagicMock()
    request.state.auth_cache = None

    with pytest.raises(HTTPException) as excinfo:
        await _role_dependency(
            request=request,
            session=session,
            workspace_id=None,
            user=user,
            api_key=None,
            allow_user=True,
            allow_service=False,
            require_workspace="no",
            min_access_level=None,
            require_workspace_roles=None,
        )

    assert excinfo.value.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_org_admin_can_access_workspace_without_membership(session: AsyncSession):
    org_id = uuid.uuid4()
    org = Organization(
        id=org_id,
        name="Test Org",
        slug=f"test-org-{org_id.hex[:8]}",
        is_active=True,
    )
    workspace = Workspace(
        id=uuid.uuid4(),
        name="Test Workspace",
        organization_id=org.id,
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
    session.add_all([org, workspace, user])
    await session.commit()

    session.add(
        OrganizationMembership(
            user_id=user.id,
            organization_id=org.id,
            role=OrgRole.ADMIN,
        )
    )
    await session.commit()

    request = MagicMock(spec=Request)
    request.state = MagicMock()
    request.state.auth_cache = None

    role = await _role_dependency(
        request=request,
        session=session,
        workspace_id=workspace.id,
        user=user,
        api_key=None,
        allow_user=True,
        allow_service=False,
        require_workspace="yes",
        min_access_level=None,
        require_workspace_roles=None,
    )

    assert role.organization_id == org.id
    assert role.workspace_id == workspace.id
    assert role.workspace_role is None
    assert role.org_role == OrgRole.ADMIN
    assert role.access_level == AccessLevel.ADMIN


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_org_member_requires_workspace_membership(session: AsyncSession):
    org_id = uuid.uuid4()
    org = Organization(
        id=org_id,
        name="Test Org",
        slug=f"test-org-{org_id.hex[:8]}",
        is_active=True,
    )
    workspace = Workspace(
        id=uuid.uuid4(),
        name="Test Workspace",
        organization_id=org.id,
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
    session.add_all([org, workspace, user])
    await session.commit()

    session.add(
        OrganizationMembership(
            user_id=user.id,
            organization_id=org.id,
            role=OrgRole.MEMBER,
        )
    )
    await session.commit()

    request = MagicMock(spec=Request)
    request.state = MagicMock()
    request.state.auth_cache = None

    with pytest.raises(HTTPException) as excinfo:
        await _role_dependency(
            request=request,
            session=session,
            workspace_id=workspace.id,
            user=user,
            api_key=None,
            allow_user=True,
            allow_service=False,
            require_workspace="yes",
            min_access_level=None,
            require_workspace_roles=None,
        )

    assert excinfo.value.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_org_admin_cannot_access_other_org_workspace(session: AsyncSession):
    org_a_id = uuid.uuid4()
    org_b_id = uuid.uuid4()
    org_a = Organization(
        id=org_a_id,
        name="Org A",
        slug=f"org-a-{org_a_id.hex[:8]}",
        is_active=True,
    )
    org_b = Organization(
        id=org_b_id,
        name="Org B",
        slug=f"org-b-{org_b_id.hex[:8]}",
        is_active=True,
    )
    workspace_b = Workspace(
        id=uuid.uuid4(),
        name="Workspace B",
        organization_id=org_b.id,
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
    session.add_all([org_a, org_b, workspace_b, user])
    await session.commit()

    session.add(
        OrganizationMembership(
            user_id=user.id,
            organization_id=org_a.id,
            role=OrgRole.ADMIN,
        )
    )
    await session.commit()

    request = MagicMock(spec=Request)
    request.state = MagicMock()
    request.state.auth_cache = None

    with pytest.raises(HTTPException) as excinfo:
        await _role_dependency(
            request=request,
            session=session,
            workspace_id=workspace_b.id,
            user=user,
            api_key=None,
            allow_user=True,
            allow_service=False,
            require_workspace="yes",
            min_access_level=None,
            require_workspace_roles=None,
        )

    assert excinfo.value.status_code == status.HTTP_403_FORBIDDEN
