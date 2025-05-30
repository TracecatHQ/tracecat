import time
import uuid
from statistics import mean
from unittest.mock import Mock

import pytest
from fastapi import Request
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat import config
from tracecat.auth.credentials import _role_dependency
from tracecat.auth.models import UserRole
from tracecat.authz.models import WorkspaceRole
from tracecat.authz.service import MembershipService, _get_membership_cached
from tracecat.db.schemas import Membership, User, Workflow, Workspace
from tracecat.types.auth import AccessLevel


@pytest.fixture
async def test_users_for_perf(db):
    """Create test users: one admin, one basic."""
    # Use a separate session to ensure data is committed and visible
    from tracecat.db.engine import get_async_session_context_manager

    # Generate unique emails to avoid conflicts
    timestamp = int(time.time() * 1000)

    async with get_async_session_context_manager() as session:
        admin_user = User(
            id=uuid.uuid4(),
            email=f"admin-perf-{timestamp}@test.com",
            hashed_password="hashed",
            role=UserRole.ADMIN,
            is_verified=True,
            is_active=True,
            is_superuser=False,
            last_login_at=None,
        )
        basic_user = User(
            id=uuid.uuid4(),
            email=f"basic-perf-{timestamp}@test.com",
            hashed_password="hashed",
            role=UserRole.BASIC,
            is_verified=True,
            is_active=True,
            is_superuser=False,
            last_login_at=None,
        )

        session.add(admin_user)
        session.add(basic_user)
        await session.commit()

        # Refresh to get complete objects
        await session.refresh(admin_user)
        await session.refresh(basic_user)

        # Return the IDs and user data
        return {
            "admin": {
                "id": admin_user.id,
                "email": admin_user.email,
                "role": admin_user.role,
            },
            "basic": {
                "id": basic_user.id,
                "email": basic_user.email,
                "role": basic_user.role,
            },
        }


@pytest.fixture
async def test_workspace_for_perf(db, test_users_for_perf):
    """Create workspace with member for performance test."""
    from tracecat.db.engine import get_async_session_context_manager

    async with get_async_session_context_manager() as session:
        # Create workspace
        workspace = Workspace(
            id=uuid.uuid4(),
            name="perf-test-workspace",
            owner_id=config.TRACECAT__DEFAULT_ORG_ID,
        )
        session.add(workspace)
        await session.commit()  # Commit workspace first

        # Add basic user as workspace member
        membership = Membership(
            user_id=test_users_for_perf["basic"]["id"],
            workspace_id=workspace.id,
            role=WorkspaceRole.EDITOR,
        )
        session.add(membership)

        # Create some workflows
        for i in range(5):
            workflow = Workflow(
                id=uuid.uuid4(),
                title=f"Perf Test Workflow {i}",
                description=f"Test workflow {i} for performance testing",
                owner_id=workspace.id,
                status="offline",
            )
            session.add(workflow)

        await session.commit()

        # Clear any cache before test
        _get_membership_cached.cache_clear()

        return workspace.id, test_users_for_perf


async def time_role_dependency_call_with_new_session(
    user_data: dict, workspace_id: uuid.UUID
) -> float:
    """Time a single _role_dependency call with a fresh session."""
    from tracecat.db.engine import get_async_session_context_manager

    # Mock request object with required headers attribute
    mock_request = Mock(spec=Request)
    mock_request.headers = {}

    # Recreate user object
    user = User(
        id=user_data["id"],
        email=user_data["email"],
        hashed_password="hashed",
        role=user_data["role"],
        is_verified=True,
        is_active=True,
        is_superuser=False,
        last_login_at=None,
    )

    start_time = time.perf_counter()

    async with get_async_session_context_manager() as session:
        # This is the core function that gets called on every API request
        await _role_dependency(
            request=mock_request,
            session=session,
            workspace_id=workspace_id,
            user=user,
            api_key=None,
            allow_user=True,
            allow_service=False,
            require_workspace="yes",
            min_access_level=None,
            require_workspace_roles=None,
        )

    end_time = time.perf_counter()
    return (end_time - start_time) * 1000  # Convert to milliseconds


@pytest.mark.anyio
async def test_membership_check_performance_impact(
    db,  # Ensure database is created
    test_workspace_for_perf,
):
    """Test that membership checks cause measurable slowdown for basic users vs admin users."""
    workspace_id, users_data = test_workspace_for_perf

    # Warm up - run a few calls first to ensure connections are established and cache is primed
    for _ in range(3):
        await time_role_dependency_call_with_new_session(
            users_data["admin"], workspace_id
        )
        await time_role_dependency_call_with_new_session(
            users_data["basic"], workspace_id
        )

    # Number of iterations to get reliable timing
    iterations = 20

    # Time admin user requests (should be fast - no membership check)
    admin_times = []
    for _ in range(iterations):
        duration = await time_role_dependency_call_with_new_session(
            users_data["admin"], workspace_id
        )
        admin_times.append(duration)

    # Clear cache before testing basic user to ensure first call hits the database
    _get_membership_cached.cache_clear()

    # Time basic user requests (first should be slow, rest should be fast due to cache)
    basic_times = []
    basic_times_first_5 = []
    basic_times_cached = []

    for i in range(iterations):
        duration = await time_role_dependency_call_with_new_session(
            users_data["basic"], workspace_id
        )
        basic_times.append(duration)
        if i < 5:
            basic_times_first_5.append(duration)
        else:
            basic_times_cached.append(duration)

    # Calculate averages
    avg_admin_time = mean(admin_times)
    avg_basic_time = mean(basic_times)
    avg_basic_first_5 = mean(basic_times_first_5) if basic_times_first_5 else 0
    avg_basic_cached = mean(basic_times_cached) if basic_times_cached else 0

    print("\n=== Performance Test Results ===")
    print(f"Admin user average time: {avg_admin_time:.2f}ms")
    print(f"Basic user average time (all): {avg_basic_time:.2f}ms")
    print(f"Basic user first 5 calls: {avg_basic_first_5:.2f}ms")
    print(f"Basic user cached calls: {avg_basic_cached:.2f}ms")
    print(f"Overall slowdown factor: {avg_basic_time / avg_admin_time:.2f}x")
    print(f"First call slowdown: {basic_times[0] / avg_admin_time:.2f}x")
    print(
        f"Cached speedup: {avg_basic_first_5 / avg_basic_cached:.2f}x faster when cached"
    )
    print("================================\n")

    # The first basic user call should be much slower (database hit)
    assert basic_times[0] > avg_admin_time * 2, (
        "First basic user call should be significantly slower"
    )

    # Cached calls should be much faster
    if len(basic_times_cached) > 0:
        assert avg_basic_cached < avg_basic_first_5, (
            "Cached calls should be faster than initial calls"
        )


@pytest.fixture
async def test_users(session: AsyncSession):
    """Create test users: one admin, one basic."""
    admin_user = User(
        id=uuid.uuid4(),
        email="admin@test.com",
        hashed_password="hashed",
        role=UserRole.ADMIN,
        is_verified=True,
        is_active=True,
        is_superuser=False,
        last_login_at=None,
    )
    basic_user = User(
        id=uuid.uuid4(),
        email="basic@test.com",
        hashed_password="hashed",
        role=UserRole.BASIC,
        is_verified=True,
        is_active=True,
        is_superuser=False,
        last_login_at=None,
    )

    session.add(admin_user)
    session.add(basic_user)
    await session.commit()

    return admin_user, basic_user


@pytest.fixture
async def test_workspace_with_members(
    session: AsyncSession, test_users, svc_workspace: Workspace
):
    """Create workspace and add basic user as member."""
    admin_user, basic_user = test_users

    # Add basic user as workspace member
    membership = Membership(
        user_id=basic_user.id,
        workspace_id=svc_workspace.id,
        role=WorkspaceRole.EDITOR,
    )
    session.add(membership)
    await session.commit()
    await session.refresh(membership)  # Ensure membership is fully loaded

    return svc_workspace, admin_user, basic_user


@pytest.fixture
async def test_workspace_with_workflows(
    session: AsyncSession, test_workspace_with_members
):
    """Create workflows in the workspace to simulate real-world scenario."""
    workspace, admin_user, basic_user = test_workspace_with_members

    # Create multiple workflows to simulate a real workspace
    workflows = []
    for i in range(5):
        workflow = Workflow(
            id=uuid.uuid4(),
            title=f"Test Workflow {i}",
            description=f"Test workflow {i} for performance testing",
            owner_id=workspace.id,
            status="offline",
        )
        session.add(workflow)
        workflows.append(workflow)

    await session.commit()
    return workspace, admin_user, basic_user, workflows


async def time_role_dependency_call(
    session: AsyncSession, user: User, workspace_id: uuid.UUID
) -> float:
    """Time a single _role_dependency call."""
    # Mock request object with required headers attribute
    mock_request = Mock(spec=Request)
    mock_request.headers = {}

    start_time = time.perf_counter()

    # This is the core function that gets called on every API request
    await _role_dependency(
        request=mock_request,
        session=session,
        workspace_id=workspace_id,
        user=user,
        api_key=None,
        allow_user=True,
        allow_service=False,
        require_workspace="yes",
        min_access_level=None,
        require_workspace_roles=None,
    )

    end_time = time.perf_counter()
    return (end_time - start_time) * 1000  # Convert to milliseconds


@pytest.mark.anyio
async def test_membership_query_is_called_for_basic_users(
    db,  # Ensure database is created
    session: AsyncSession,
    test_workspace_with_members,
):
    """Test that membership queries are actually being made for basic users."""
    workspace, admin_user, basic_user = test_workspace_with_members

    # Create a membership service to verify the query works
    membership_svc = MembershipService(session)

    # Verify basic user has membership
    membership = await membership_svc.get_membership(workspace.id, basic_user.id)
    assert membership is not None, "Basic user should have workspace membership"
    assert membership.role == WorkspaceRole.EDITOR

    # Verify admin user doesn't need membership (would return None if queried)
    admin_membership = await membership_svc.get_membership(workspace.id, admin_user.id)
    assert admin_membership is None, (
        "Admin user should not need explicit workspace membership"
    )


@pytest.mark.anyio
async def test_basic_user_without_membership_fails(
    db,  # Ensure database is created
    session: AsyncSession,
    svc_workspace: Workspace,
    test_users,
):
    """Test that basic users without workspace membership are rejected."""
    admin_user, basic_user = test_users

    # Mock request object
    mock_request = Mock(spec=Request)
    mock_request.headers = {}

    # Admin user should work even without explicit membership
    role = await _role_dependency(
        request=mock_request,
        session=session,
        workspace_id=svc_workspace.id,
        user=admin_user,
        api_key=None,
        allow_user=True,
        allow_service=False,
        require_workspace="yes",
        min_access_level=None,
        require_workspace_roles=None,
    )
    assert role.access_level == AccessLevel.ADMIN

    # Basic user without membership should be rejected
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        await _role_dependency(
            request=mock_request,
            session=session,
            workspace_id=svc_workspace.id,
            user=basic_user,
            api_key=None,
            allow_user=True,
            allow_service=False,
            require_workspace="yes",
            min_access_level=None,
            require_workspace_roles=None,
        )

    assert exc_info.value.status_code == 403
    assert "Forbidden" in str(exc_info.value.detail)
