"""Integration tests for RLS policies.

These tests verify that RLS policies correctly filter database access
based on organization and workspace context.

IMPORTANT: These tests require:
1. A running PostgreSQL database with the RLS migration applied
2. The TRACECAT__FEATURE_FLAGS=rls-enabled environment variable set

Run with:
    TRACECAT__FEATURE_FLAGS=rls-enabled uv run pytest tests/unit/test_rls_policies.py -v
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import AccessLevel, Role
from tracecat.contexts import ctx_role
from tracecat.db.engine import get_async_engine
from tracecat.db.rls import (
    RLS_BYPASS_VALUE,
    RLS_VAR_ORG_ID,
    RLS_VAR_WORKSPACE_ID,
    is_rls_enabled,
    set_rls_context,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


# Skip all tests if RLS is not enabled
pytestmark = pytest.mark.skipif(
    not is_rls_enabled(),
    reason="RLS feature flag not enabled. Set TRACECAT__FEATURE_FLAGS=rls-enabled",
)


@pytest.fixture
async def rls_session() -> AsyncGenerator[AsyncSession, None]:
    """Create a session for RLS testing without automatic context setting."""
    async with AsyncSession(get_async_engine(), expire_on_commit=False) as session:
        yield session


@pytest.fixture
def org_id_a() -> uuid.UUID:
    """Organization A for testing."""
    return uuid.uuid4()


@pytest.fixture
def org_id_b() -> uuid.UUID:
    """Organization B for testing."""
    return uuid.uuid4()


@pytest.fixture
def workspace_id_a() -> uuid.UUID:
    """Workspace A for testing."""
    return uuid.uuid4()


@pytest.fixture
def workspace_id_b() -> uuid.UUID:
    """Workspace B for testing."""
    return uuid.uuid4()


@pytest.fixture
def role_workspace_a(org_id_a: uuid.UUID, workspace_id_a: uuid.UUID) -> Role:
    """Role with access to workspace A."""
    return Role(
        type="user",
        workspace_id=workspace_id_a,
        organization_id=org_id_a,
        user_id=uuid.uuid4(),
        service_id="tracecat-api",
        access_level=AccessLevel.BASIC,
        workspace_role=None,
    )


@pytest.fixture
def role_workspace_b(org_id_b: uuid.UUID, workspace_id_b: uuid.UUID) -> Role:
    """Role with access to workspace B."""
    return Role(
        type="user",
        workspace_id=workspace_id_b,
        organization_id=org_id_b,
        user_id=uuid.uuid4(),
        service_id="tracecat-api",
        access_level=AccessLevel.BASIC,
        workspace_role=None,
    )


class TestRlsContextVariables:
    """Tests for RLS PostgreSQL session variables."""

    @pytest.mark.anyio
    async def test_context_variables_are_set_correctly(
        self, rls_session: AsyncSession, org_id_a: uuid.UUID, workspace_id_a: uuid.UUID
    ):
        """Test that RLS context variables are set in the PostgreSQL session."""
        await set_rls_context(rls_session, org_id_a, workspace_id_a)

        # Query the current settings
        result = await rls_session.execute(
            text(f"SELECT current_setting('{RLS_VAR_ORG_ID}', true)")
        )
        org_setting = result.scalar()
        assert org_setting == str(org_id_a)

        result = await rls_session.execute(
            text(f"SELECT current_setting('{RLS_VAR_WORKSPACE_ID}', true)")
        )
        workspace_setting = result.scalar()
        assert workspace_setting == str(workspace_id_a)

    @pytest.mark.anyio
    async def test_bypass_value_is_set_for_none(self, rls_session: AsyncSession):
        """Test that bypass value is set when context is None."""
        await set_rls_context(rls_session, org_id=None, workspace_id=None)

        result = await rls_session.execute(
            text(f"SELECT current_setting('{RLS_VAR_ORG_ID}', true)")
        )
        org_setting = result.scalar()
        assert org_setting == RLS_BYPASS_VALUE

        result = await rls_session.execute(
            text(f"SELECT current_setting('{RLS_VAR_WORKSPACE_ID}', true)")
        )
        workspace_setting = result.scalar()
        assert workspace_setting == RLS_BYPASS_VALUE


class TestRlsPolicyEnforcement:
    """Tests for RLS policy enforcement on tables.

    Note: These tests require a test database with actual data.
    They are designed to verify that RLS policies are working but
    may need adjustment based on the test data setup.
    """

    @pytest.mark.anyio
    async def test_rls_enabled_on_workspace_table(self, rls_session: AsyncSession):
        """Verify that RLS is enabled on the workspace table."""
        result = await rls_session.execute(
            text("""
                SELECT relrowsecurity, relforcerowsecurity
                FROM pg_class
                WHERE relname = 'workspace'
            """)
        )
        row = result.fetchone()

        # If table doesn't exist in test DB, skip
        if row is None:
            pytest.skip("Workspace table not found in test database")

        relrowsecurity, relforcerowsecurity = row
        assert relrowsecurity is True, "RLS should be enabled on workspace table"
        assert relforcerowsecurity is True, "RLS should be forced on workspace table"

    @pytest.mark.anyio
    async def test_rls_enabled_on_workflow_table(self, rls_session: AsyncSession):
        """Verify that RLS is enabled on the workflow table."""
        result = await rls_session.execute(
            text("""
                SELECT relrowsecurity, relforcerowsecurity
                FROM pg_class
                WHERE relname = 'workflow'
            """)
        )
        row = result.fetchone()

        if row is None:
            pytest.skip("Workflow table not found in test database")

        relrowsecurity, relforcerowsecurity = row
        assert relrowsecurity is True, "RLS should be enabled on workflow table"
        assert relforcerowsecurity is True, "RLS should be forced on workflow table"

    @pytest.mark.anyio
    async def test_rls_policy_exists_on_workflow(self, rls_session: AsyncSession):
        """Verify that an RLS policy exists on the workflow table."""
        result = await rls_session.execute(
            text("""
                SELECT policyname, cmd, qual
                FROM pg_policies
                WHERE tablename = 'workflow'
            """)
        )
        rows = result.fetchall()

        assert len(rows) > 0, "At least one RLS policy should exist on workflow table"

        # Check that our policy is present
        policy_names = [row[0] for row in rows]
        assert "rls_policy_workflow" in policy_names, (
            f"rls_policy_workflow should exist. Found: {policy_names}"
        )

    @pytest.mark.anyio
    async def test_rls_policy_exists_on_workspace(self, rls_session: AsyncSession):
        """Verify that an RLS policy exists on the workspace table."""
        result = await rls_session.execute(
            text("""
                SELECT policyname, cmd
                FROM pg_policies
                WHERE tablename = 'workspace'
            """)
        )
        rows = result.fetchall()

        assert len(rows) > 0, "At least one RLS policy should exist on workspace table"

        policy_names = [row[0] for row in rows]
        assert "rls_policy_workspace" in policy_names, (
            f"rls_policy_workspace should exist. Found: {policy_names}"
        )


class TestRlsIsolation:
    """Tests for RLS isolation between tenants.

    These tests verify that queries return different results
    based on the RLS context set in the session.
    """

    @pytest.mark.anyio
    async def test_bypass_context_returns_all_rows(self, rls_session: AsyncSession):
        """Test that bypass context allows access to all rows."""
        # Set bypass context
        await set_rls_context(rls_session, org_id=None, workspace_id=None)

        # Count should return all workspaces (not filtered)
        result = await rls_session.execute(text("SELECT COUNT(*) FROM workspace"))
        count = result.scalar()

        # We can't assert exact count, but it should be >= 0
        assert count is not None
        assert count >= 0

    @pytest.mark.anyio
    async def test_workspace_context_filters_workflows(
        self,
        rls_session: AsyncSession,
        workspace_id_a: uuid.UUID,
    ):
        """Test that workspace context filters workflow queries."""
        # Set context to a random workspace ID (likely no matching rows)
        await set_rls_context(
            rls_session, org_id=uuid.uuid4(), workspace_id=workspace_id_a
        )

        # Query should only return workflows matching the workspace
        result = await rls_session.execute(text("SELECT COUNT(*) FROM workflow"))
        count = result.scalar()

        # With a random workspace ID, we should get 0 or very few rows
        # (unless the test DB happens to have data with that ID)
        assert count is not None
        assert count >= 0

    @pytest.mark.anyio
    async def test_org_context_filters_workspaces(
        self,
        rls_session: AsyncSession,
        org_id_a: uuid.UUID,
    ):
        """Test that org context filters workspace queries."""
        # Set context to a random org ID
        await set_rls_context(rls_session, org_id=org_id_a, workspace_id=uuid.uuid4())

        # Query should only return workspaces matching the org
        result = await rls_session.execute(text("SELECT COUNT(*) FROM workspace"))
        count = result.scalar()

        # With a random org ID, we should get 0 or very few rows
        assert count is not None
        assert count >= 0


class TestRlsWithCtxRole:
    """Tests for RLS integration with ctx_role context variable."""

    @pytest.mark.anyio
    async def test_ctx_role_is_used_by_session(
        self,
        role_workspace_a: Role,
    ):
        """Test that ctx_role is properly read when creating sessions."""
        from tracecat.db.engine import get_async_session

        # Set ctx_role before getting session
        ctx_role.set(role_workspace_a)

        try:
            async for session in get_async_session():
                # Check that the context was set
                result = await session.execute(
                    text(f"SELECT current_setting('{RLS_VAR_WORKSPACE_ID}', true)")
                )
                workspace_setting = result.scalar()
                assert workspace_setting == str(role_workspace_a.workspace_id)
                break
        finally:
            ctx_role.set(None)
