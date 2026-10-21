"""Unit tests for RLS context management."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import nullcontext
from unittest.mock import AsyncMock, MagicMock

import pytest

from tracecat import config
from tracecat.auth.types import Role
from tracecat.authz.membership import mirror_workspace_membership
from tracecat.contexts import ctx_role
from tracecat.db.rls import (
    RLS_BYPASS_OFF,
    RLS_BYPASS_ON,
    RLS_UNSET_VALUE,
    set_rls_context,
    set_rls_context_from_role,
)


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


@pytest.mark.anyio
@pytest.mark.parametrize(
    "bypass", [None, False, True], ids=["uncached", "enforced", "bypassed"]
)
@pytest.mark.parametrize("fails", [False, True], ids=["success", "failure"])
async def test_temporary_bypass_restores_session_context(
    mock_session: AsyncMock,
    test_role: Role,
    bypass: bool | None,
    fails: bool,
) -> None:
    """Restore exact session values, including off/shadow bypass, on either exit."""
    previous_params = {
        "bypass": RLS_BYPASS_OFF,
        "org_id": RLS_UNSET_VALUE,
        "workspace_id": RLS_UNSET_VALUE,
        "user_id": RLS_UNSET_VALUE,
    }
    if bypass is not None:
        await set_rls_context(
            mock_session,
            org_id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            bypass=bypass,
        )
        previous_params = mock_session.execute.call_args.args[1].copy()
    previous_info = mock_session.sync_session.info.copy()
    mock_session.execute.reset_mock()
    if fails:
        mock_session.execute.side_effect = [None, RuntimeError("mirror failed"), None]

    # A different ambient role must never replace the saved session context.
    token = ctx_role.set(test_role)
    try:
        with (
            pytest.raises(RuntimeError, match="mirror failed")
            if fails
            else nullcontext()
        ):
            await mirror_workspace_membership(
                mock_session, user_id=uuid.uuid4(), workspace_id=uuid.uuid4()
            )
    finally:
        ctx_role.reset(token)

    calls = mock_session.execute.call_args_list
    assert calls[0].args[1]["bypass"] == RLS_BYPASS_ON
    assert calls[-1].args[1] == previous_params
    assert mock_session.sync_session.info == previous_info


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
