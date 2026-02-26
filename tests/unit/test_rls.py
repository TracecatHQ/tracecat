"""Unit tests for RLS context management."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tracecat import config
from tracecat.auth.types import Role
from tracecat.contexts import ctx_role
from tracecat.db.rls import (
    RLS_BYPASS_OFF,
    RLS_BYPASS_ON,
    RLS_UNSET_VALUE,
    clear_rls_context,
    is_rls_enabled,
    require_rls_access,
    set_rls_context,
    set_rls_context_from_role,
    verify_rls_access,
)
from tracecat.exceptions import TracecatRLSViolationError


@pytest.fixture(scope="session", autouse=True)
def workflow_bucket() -> Iterator[None]:
    """Disable MinIO-dependent workflow bucket setup for pure unit tests."""
    yield


def _assert_execute_params(
    session: AsyncMock,
    *,
    bypass: str,
    org_id: str,
    workspace_id: str,
    user_id: str,
) -> None:
    """Assert that RLS context is applied via one execute call with expected params."""
    session.execute.assert_called_once()
    params = session.execute.call_args[0][1]
    assert params["bypass"] == bypass
    assert params["org_id"] == org_id
    assert params["workspace_id"] == workspace_id
    assert params["user_id"] == user_id


@pytest.fixture
def mock_session() -> AsyncMock:
    """Create a mock async session."""
    session = AsyncMock()
    session.sync_session = MagicMock()
    session.sync_session.info = {}
    return session


@pytest.fixture
def test_role() -> Role:
    """Create a test role with org and workspace."""
    return Role(
        type="user",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        service_id="tracecat-api",
    )


@pytest.fixture
def system_role() -> Role:
    """Create a system role without workspace."""
    return Role(
        type="service",
        workspace_id=None,
        organization_id=uuid.uuid4(),
        user_id=None,
        service_id="tracecat-api",
    )


@pytest.fixture
def superuser_role() -> Role:
    """Create a platform superuser role."""
    return Role(
        type="user",
        workspace_id=None,
        organization_id=None,
        user_id=uuid.uuid4(),
        service_id="tracecat-api",
        is_platform_superuser=True,
    )


class TestIsRlsEnabled:
    """Tests for is_rls_enabled function."""

    def test_returns_false_when_mode_is_off(self, monkeypatch: pytest.MonkeyPatch):
        """Test that RLS is disabled when mode is off."""
        monkeypatch.setattr(
            "tracecat.db.rls.config.TRACECAT__RLS_MODE", config.RLSMode.OFF
        )
        assert is_rls_enabled() is False

    def test_returns_true_when_mode_is_shadow(self, monkeypatch: pytest.MonkeyPatch):
        """Test that RLS is enabled when mode is shadow."""
        monkeypatch.setattr(
            "tracecat.db.rls.config.TRACECAT__RLS_MODE", config.RLSMode.SHADOW
        )
        assert is_rls_enabled() is True

    def test_returns_true_when_mode_is_enforce(self, monkeypatch: pytest.MonkeyPatch):
        """Test that RLS is enabled when mode is enforce."""
        monkeypatch.setattr(
            "tracecat.db.rls.config.TRACECAT__RLS_MODE", config.RLSMode.ENFORCE
        )
        assert is_rls_enabled() is True


class TestSetRlsContext:
    """Tests for set_rls_context function."""

    @pytest.mark.anyio
    async def test_applies_context_when_mode_is_off(
        self, mock_session: AsyncMock, monkeypatch: pytest.MonkeyPatch
    ):
        """set_rls_context should still apply GUCs when mode is off."""
        monkeypatch.setattr(
            "tracecat.db.rls.config.TRACECAT__RLS_MODE", config.RLSMode.OFF
        )

        org_id = uuid.uuid4()
        workspace_id = uuid.uuid4()
        user_id = uuid.uuid4()

        await set_rls_context(
            mock_session,
            org_id=org_id,
            workspace_id=workspace_id,
            user_id=user_id,
        )

        _assert_execute_params(
            mock_session,
            bypass=RLS_BYPASS_OFF,
            org_id=str(org_id),
            workspace_id=str(workspace_id),
            user_id=str(user_id),
        )

    @pytest.mark.anyio
    async def test_sets_context_variables(self, mock_session: AsyncMock):
        """Test that RLS context variables are set correctly."""
        org_id = uuid.uuid4()
        workspace_id = uuid.uuid4()
        user_id = uuid.uuid4()

        await set_rls_context(
            mock_session,
            org_id=org_id,
            workspace_id=workspace_id,
            user_id=user_id,
        )

        _assert_execute_params(
            mock_session,
            bypass=RLS_BYPASS_OFF,
            org_id=str(org_id),
            workspace_id=str(workspace_id),
            user_id=str(user_id),
        )

    @pytest.mark.anyio
    async def test_resets_tenant_context_for_none_values(self, mock_session: AsyncMock):
        """Test that None values reset tenant variables while bypass stays off."""
        await set_rls_context(
            mock_session,
            org_id=None,
            workspace_id=None,
            user_id=None,
        )

        _assert_execute_params(
            mock_session,
            bypass=RLS_BYPASS_OFF,
            org_id=RLS_UNSET_VALUE,
            workspace_id=RLS_UNSET_VALUE,
            user_id=RLS_UNSET_VALUE,
        )


class TestSetRlsContextFromRole:
    """Tests for set_rls_context_from_role function."""

    @pytest.mark.anyio
    async def test_applies_context_when_mode_is_off(
        self,
        mock_session: AsyncMock,
        test_role: Role,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """set_rls_context_from_role should still apply GUCs when mode is off."""
        monkeypatch.setattr(
            "tracecat.db.rls.config.TRACECAT__RLS_MODE", config.RLSMode.OFF
        )

        await set_rls_context_from_role(mock_session, test_role)

        _assert_execute_params(
            mock_session,
            bypass=RLS_BYPASS_OFF,
            org_id=str(test_role.organization_id),
            workspace_id=str(test_role.workspace_id),
            user_id=str(test_role.user_id),
        )

    @pytest.mark.anyio
    async def test_uses_provided_role(
        self,
        mock_session: AsyncMock,
        test_role: Role,
    ):
        """Test that provided role is used to set context."""
        await set_rls_context_from_role(mock_session, test_role)

        _assert_execute_params(
            mock_session,
            bypass=RLS_BYPASS_OFF,
            org_id=str(test_role.organization_id),
            workspace_id=str(test_role.workspace_id),
            user_id=str(test_role.user_id),
        )

    @pytest.mark.anyio
    async def test_reads_from_ctx_role_when_no_role_provided(
        self,
        mock_session: AsyncMock,
        test_role: Role,
    ):
        """Test that ctx_role is used when no role is provided."""
        # Set ctx_role
        ctx_role.set(test_role)

        try:
            await set_rls_context_from_role(mock_session)

            _assert_execute_params(
                mock_session,
                bypass=RLS_BYPASS_OFF,
                org_id=str(test_role.organization_id),
                workspace_id=str(test_role.workspace_id),
                user_id=str(test_role.user_id),
            )
        finally:
            ctx_role.set(None)

    @pytest.mark.anyio
    async def test_sets_deny_default_when_no_role_available(
        self,
        mock_session: AsyncMock,
    ):
        """Test that no-role context sets bypass off and resets tenant IDs."""
        # Ensure ctx_role is None
        ctx_role.set(None)

        await set_rls_context_from_role(mock_session)

        _assert_execute_params(
            mock_session,
            bypass=RLS_BYPASS_OFF,
            org_id=RLS_UNSET_VALUE,
            workspace_id=RLS_UNSET_VALUE,
            user_id=RLS_UNSET_VALUE,
        )

    @pytest.mark.anyio
    async def test_sets_bypass_for_platform_superuser(
        self,
        mock_session: AsyncMock,
        superuser_role: Role,
    ):
        """Test that platform superusers get explicit bypass context."""
        await set_rls_context_from_role(mock_session, superuser_role)

        _assert_execute_params(
            mock_session,
            bypass=RLS_BYPASS_ON,
            org_id=RLS_UNSET_VALUE,
            workspace_id=RLS_UNSET_VALUE,
            user_id=str(superuser_role.user_id),
        )


class TestClearRlsContext:
    """Tests for clear_rls_context function."""

    @pytest.mark.anyio
    async def test_clears_context_when_mode_is_off(
        self, mock_session: AsyncMock, monkeypatch: pytest.MonkeyPatch
    ):
        """clear_rls_context should still enforce deny-default when mode is off."""
        monkeypatch.setattr(
            "tracecat.db.rls.config.TRACECAT__RLS_MODE", config.RLSMode.OFF
        )

        await clear_rls_context(mock_session)

        _assert_execute_params(
            mock_session,
            bypass=RLS_BYPASS_OFF,
            org_id=RLS_UNSET_VALUE,
            workspace_id=RLS_UNSET_VALUE,
            user_id=RLS_UNSET_VALUE,
        )

    @pytest.mark.anyio
    async def test_sets_deny_default_values(self, mock_session: AsyncMock):
        """Test that clearing enforces bypass-off and no tenant scope."""
        await clear_rls_context(mock_session)

        _assert_execute_params(
            mock_session,
            bypass=RLS_BYPASS_OFF,
            org_id=RLS_UNSET_VALUE,
            workspace_id=RLS_UNSET_VALUE,
            user_id=RLS_UNSET_VALUE,
        )


class TestVerifyRlsAccess:
    """Tests for verify_rls_access function."""

    @pytest.mark.anyio
    async def test_returns_true_when_record_found(self, mock_session: AsyncMock):
        """Test that verify_rls_access returns True when record is accessible."""
        # Mock query result to return a record
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = MagicMock()
        mock_session.execute.return_value = mock_result

        # Use actual SQLAlchemy model for testing
        from tracecat.db.models import Workflow

        result = await verify_rls_access(mock_session, Workflow, uuid.uuid4())

        assert result is True
        mock_session.execute.assert_called_once()

    @pytest.mark.anyio
    async def test_returns_false_when_record_not_found(self, mock_session: AsyncMock):
        """Test that verify_rls_access returns False when RLS blocks access."""
        # Mock query result to return None (RLS blocked)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        # Use actual SQLAlchemy model for testing
        from tracecat.db.models import Workflow

        result = await verify_rls_access(mock_session, Workflow, uuid.uuid4())

        assert result is False
        mock_session.execute.assert_called_once()


class TestRequireRlsAccess:
    """Tests for require_rls_access function."""

    @pytest.mark.anyio
    async def test_noop_when_rls_disabled(
        self, mock_session: AsyncMock, monkeypatch: pytest.MonkeyPatch
    ):
        """Test that require_rls_access does nothing when RLS is disabled."""
        monkeypatch.setattr("tracecat.db.rls.is_rls_enabled", lambda: False)

        from tracecat.db.models import Workflow

        # Should not raise even without setting up mock
        await require_rls_access(mock_session, Workflow, uuid.uuid4())

    @pytest.mark.anyio
    async def test_passes_when_access_allowed(
        self, mock_session: AsyncMock, monkeypatch: pytest.MonkeyPatch
    ):
        """Test that require_rls_access passes when access is allowed."""
        monkeypatch.setattr(
            "tracecat.db.rls.config.TRACECAT__RLS_MODE", config.RLSMode.ENFORCE
        )

        from tracecat.db.models import Workflow

        # Mock query result to return a record
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = MagicMock()
        mock_session.execute.return_value = mock_result

        # Should not raise
        await require_rls_access(mock_session, Workflow, uuid.uuid4())

    @pytest.mark.anyio
    async def test_raises_when_access_denied(
        self, mock_session: AsyncMock, test_role: Role, monkeypatch: pytest.MonkeyPatch
    ):
        """Test that require_rls_access raises TracecatRLSViolationError when access is denied."""
        monkeypatch.setattr(
            "tracecat.db.rls.config.TRACECAT__RLS_MODE", config.RLSMode.ENFORCE
        )

        from tracecat.db.models import Workflow

        # Set role context
        ctx_role.set(test_role)

        # Mock query result to return None (RLS blocked)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        try:
            with pytest.raises(TracecatRLSViolationError) as exc_info:
                await require_rls_access(
                    mock_session, Workflow, uuid.uuid4(), operation="delete"
                )

            assert exc_info.value.table == "workflow"
            assert exc_info.value.operation == "delete"
            assert exc_info.value.org_id == str(test_role.organization_id)
            assert exc_info.value.workspace_id == str(test_role.workspace_id)
        finally:
            ctx_role.set(None)

    @pytest.mark.anyio
    async def test_logs_violation_on_access_denied(
        self, mock_session: AsyncMock, test_role: Role, monkeypatch: pytest.MonkeyPatch
    ):
        """Test that violations are audited when access is denied."""
        monkeypatch.setattr(
            "tracecat.db.rls.config.TRACECAT__RLS_MODE", config.RLSMode.ENFORCE
        )

        from tracecat.db.models import Workflow

        ctx_role.set(test_role)

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        with patch("tracecat.db.rls.audit_rls_violation") as mock_audit:
            try:
                with pytest.raises(TracecatRLSViolationError):
                    await require_rls_access(
                        mock_session, Workflow, uuid.uuid4(), operation="update"
                    )

                mock_audit.assert_called_once()
                call_kwargs = mock_audit.call_args[1]
                assert call_kwargs["table"] == "workflow"
                assert call_kwargs["operation"] == "update"
            finally:
                ctx_role.set(None)
