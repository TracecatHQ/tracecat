"""Regression tests for workspace_role propagation via Role headers.

These tests verify the fix for the regression introduced in 0.52.0 where
workspace_role was not included in Role.to_headers() or read by
_authenticate_service(), causing table operations via the executor to fail.
"""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from tracecat.auth.credentials import _authenticate_service
from tracecat.auth.types import Role
from tracecat.authz.enums import WorkspaceRole


class MockHeaders(dict):
    """Dict subclass that can be used as request.headers mock."""

    pass


class TestRoleToHeaders:
    """Tests for Role.to_headers() method."""

    def test_to_headers_includes_workspace_role_editor(self):
        """Verify workspace_role is included in headers when set to EDITOR."""
        role = Role(
            type="service",
            service_id="tracecat-runner",
            workspace_id=uuid4(),
            workspace_role=WorkspaceRole.EDITOR,
        )
        headers = role.to_headers()
        assert "x-tracecat-role-workspace-role" in headers
        assert headers["x-tracecat-role-workspace-role"] == "editor"

    def test_to_headers_includes_workspace_role_admin(self):
        """Verify workspace_role is included in headers when set to ADMIN."""
        role = Role(
            type="service",
            service_id="tracecat-runner",
            workspace_id=uuid4(),
            workspace_role=WorkspaceRole.ADMIN,
        )
        headers = role.to_headers()
        assert "x-tracecat-role-workspace-role" in headers
        assert headers["x-tracecat-role-workspace-role"] == "admin"

    def test_to_headers_excludes_workspace_role_when_none(self):
        """Verify workspace_role header is not included when None."""
        role = Role(
            type="service",
            service_id="tracecat-runner",
        )
        headers = role.to_headers()
        assert "x-tracecat-role-workspace-role" not in headers

    def test_to_headers_roundtrip_preserves_workspace_role(self):
        """Verify workspace_role survives a roundtrip through headers."""
        workspace_id = uuid4()
        organization_id = uuid4()
        user_id = uuid4()
        original_role = Role(
            type="service",
            service_id="tracecat-runner",
            workspace_id=workspace_id,
            organization_id=organization_id,
            workspace_role=WorkspaceRole.EDITOR,
            user_id=user_id,
        )
        headers = original_role.to_headers()

        # Verify all expected headers are present
        assert headers["x-tracecat-role-type"] == "service"
        assert headers["x-tracecat-role-service-id"] == "tracecat-runner"
        assert "x-tracecat-role-scopes" not in headers
        assert headers["x-tracecat-role-user-id"] == str(user_id)
        assert headers["x-tracecat-role-workspace-id"] == str(workspace_id)
        assert headers["x-tracecat-role-organization-id"] == str(organization_id)
        assert headers["x-tracecat-role-workspace-role"] == "editor"


@pytest.mark.anyio
class TestAuthenticateServiceWorkspaceRole:
    """Tests for _authenticate_service() reading workspace_role from headers."""

    async def test_authenticate_service_reads_workspace_role_editor(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Verify _authenticate_service reads workspace_role EDITOR from headers."""
        # Mock the service key
        monkeypatch.setattr(
            "tracecat.auth.credentials.config.TRACECAT__SERVICE_KEY", "test-key"
        )
        monkeypatch.setattr(
            "tracecat.auth.credentials.config.TRACECAT__SERVICE_ROLES_WHITELIST",
            ["tracecat-runner"],
        )

        workspace_id = str(uuid4())
        request = MagicMock()
        request.headers = MockHeaders(
            {
                "x-tracecat-role-service-id": "tracecat-runner",
                "x-tracecat-role-scopes": "workflow:read",
                "x-tracecat-role-workspace-id": workspace_id,
                "x-tracecat-role-workspace-role": "editor",
            }
        )

        role = await _authenticate_service(request, api_key="test-key")

        assert role is not None
        assert role.workspace_role == WorkspaceRole.EDITOR

    async def test_authenticate_service_reads_workspace_role_admin(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Verify _authenticate_service reads workspace_role ADMIN from headers."""
        monkeypatch.setattr(
            "tracecat.auth.credentials.config.TRACECAT__SERVICE_KEY", "test-key"
        )
        monkeypatch.setattr(
            "tracecat.auth.credentials.config.TRACECAT__SERVICE_ROLES_WHITELIST",
            ["tracecat-runner"],
        )

        workspace_id = str(uuid4())
        request = MagicMock()
        request.headers = MockHeaders(
            {
                "x-tracecat-role-service-id": "tracecat-runner",
                "x-tracecat-role-scopes": "workflow:read",
                "x-tracecat-role-workspace-id": workspace_id,
                "x-tracecat-role-workspace-role": "admin",
            }
        )

        role = await _authenticate_service(request, api_key="test-key")

        assert role is not None
        assert role.workspace_role == WorkspaceRole.ADMIN

    async def test_authenticate_service_no_workspace_role_header(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Verify _authenticate_service handles missing workspace_role header."""
        monkeypatch.setattr(
            "tracecat.auth.credentials.config.TRACECAT__SERVICE_KEY", "test-key"
        )
        monkeypatch.setattr(
            "tracecat.auth.credentials.config.TRACECAT__SERVICE_ROLES_WHITELIST",
            ["tracecat-runner"],
        )

        request = MagicMock()
        request.headers = MockHeaders(
            {
                "x-tracecat-role-service-id": "tracecat-runner",
                "x-tracecat-role-scopes": "workflow:read",
            }
        )

        role = await _authenticate_service(request, api_key="test-key")

        assert role is not None
        assert role.workspace_role is None

    async def test_authenticate_service_full_roundtrip(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Verify full roundtrip: Role -> to_headers() -> _authenticate_service() preserves workspace_role."""
        monkeypatch.setattr(
            "tracecat.auth.credentials.config.TRACECAT__SERVICE_KEY", "test-key"
        )
        monkeypatch.setattr(
            "tracecat.auth.credentials.config.TRACECAT__SERVICE_ROLES_WHITELIST",
            ["tracecat-runner"],
        )

        # Create original role with workspace_role
        workspace_id = uuid4()
        organization_id = uuid4()
        original_role = Role(
            type="service",
            service_id="tracecat-runner",
            workspace_id=workspace_id,
            organization_id=organization_id,
            workspace_role=WorkspaceRole.EDITOR,
        )

        # Convert to headers
        headers = MockHeaders(original_role.to_headers())

        # Create mock request with those headers
        request = MagicMock()
        request.headers = headers

        # Reconstruct role from headers
        reconstructed_role = await _authenticate_service(request, api_key="test-key")

        # Verify workspace_role is preserved
        assert reconstructed_role is not None
        assert reconstructed_role.workspace_role == original_role.workspace_role
        assert reconstructed_role.workspace_role == WorkspaceRole.EDITOR
        assert str(reconstructed_role.workspace_id) == str(original_role.workspace_id)
        assert str(reconstructed_role.organization_id) == str(
            original_role.organization_id
        )

    async def test_authenticate_service_derives_org_from_workspace_when_missing_header(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Verify _authenticate_service derives organization_id from workspace_id."""
        monkeypatch.setattr(
            "tracecat.auth.credentials.config.TRACECAT__SERVICE_KEY", "test-key"
        )
        monkeypatch.setattr(
            "tracecat.auth.credentials.config.TRACECAT__SERVICE_ROLES_WHITELIST",
            ["tracecat-runner"],
        )
        derived_org_id = uuid4()
        monkeypatch.setattr(
            "tracecat.auth.credentials._get_workspace_org_id",
            AsyncMock(return_value=derived_org_id),
        )

        request = MagicMock()
        request.headers = MockHeaders(
            {
                "x-tracecat-role-service-id": "tracecat-runner",
                "x-tracecat-role-scopes": "workflow:read",
                "x-tracecat-role-workspace-id": str(uuid4()),
            }
        )

        role = await _authenticate_service(request, api_key="test-key")

        assert role is not None
        assert role.organization_id == derived_org_id
