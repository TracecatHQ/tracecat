"""Tests for Role header serialization and _authenticate_service() roundtrip.

Validates that Role.to_headers() correctly serializes all current fields
and that _authenticate_service() reconstructs them from request headers.
"""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from tracecat.auth.credentials import _authenticate_service
from tracecat.auth.types import Role


class MockHeaders(dict):
    """Dict subclass that can be used as request.headers mock."""

    pass


class TestRoleToHeaders:
    """Tests for Role.to_headers() method."""

    def test_to_headers_includes_all_fields(self):
        """Verify to_headers() serializes type, service_id, user_id, workspace_id, organization_id, and scopes."""
        workspace_id = uuid4()
        organization_id = uuid4()
        user_id = uuid4()
        role = Role(
            type="service",
            service_id="tracecat-runner",
            workspace_id=workspace_id,
            organization_id=organization_id,
            user_id=user_id,
            scopes=frozenset({"workflow:read", "cases:write"}),
        )
        headers = role.to_headers()

        assert headers["x-tracecat-role-type"] == "service"
        assert headers["x-tracecat-role-service-id"] == "tracecat-runner"
        assert headers["x-tracecat-role-user-id"] == str(user_id)
        assert headers["x-tracecat-role-workspace-id"] == str(workspace_id)
        assert headers["x-tracecat-role-organization-id"] == str(organization_id)
        # Scopes are sorted and comma-joined
        assert headers["x-tracecat-role-scopes"] == "cases:write,workflow:read"

    def test_to_headers_omits_none_fields(self):
        """Verify optional fields are excluded from headers when None."""
        role = Role(
            type="service",
            service_id="tracecat-runner",
        )
        headers = role.to_headers()

        assert headers["x-tracecat-role-type"] == "service"
        assert headers["x-tracecat-role-service-id"] == "tracecat-runner"
        assert "x-tracecat-role-user-id" not in headers
        assert "x-tracecat-role-workspace-id" not in headers
        assert "x-tracecat-role-organization-id" not in headers
        assert "x-tracecat-role-scopes" not in headers

    def test_to_headers_does_not_include_workspace_role(self):
        """Verify workspace_role header is never present (removed in RBAC migration)."""
        role = Role(
            type="service",
            service_id="tracecat-runner",
            workspace_id=uuid4(),
        )
        headers = role.to_headers()

        assert "x-tracecat-role-workspace-role" not in headers


@pytest.mark.anyio
class TestAuthenticateServiceRoundtrip:
    """Tests for _authenticate_service() reconstructing Role from headers."""

    async def test_roundtrip_preserves_all_fields(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Verify full roundtrip: Role -> to_headers() -> _authenticate_service() preserves fields."""
        monkeypatch.setattr(
            "tracecat.auth.credentials.config.TRACECAT__SERVICE_KEY", "test-key"
        )
        monkeypatch.setattr(
            "tracecat.auth.credentials.config.TRACECAT__SERVICE_ROLES_WHITELIST",
            ["tracecat-runner"],
        )

        workspace_id = uuid4()
        organization_id = uuid4()
        user_id = uuid4()
        original_role = Role(
            type="service",
            service_id="tracecat-runner",
            workspace_id=workspace_id,
            organization_id=organization_id,
            user_id=user_id,
            scopes=frozenset({"workflow:read"}),
        )

        headers = MockHeaders(original_role.to_headers())
        request = MagicMock()
        request.headers = headers

        reconstructed = await _authenticate_service(request, api_key="test-key")

        assert reconstructed is not None
        assert reconstructed.type == "service"
        assert reconstructed.service_id == "tracecat-runner"
        assert reconstructed.user_id == user_id
        assert reconstructed.workspace_id == workspace_id
        assert reconstructed.organization_id == organization_id
        assert reconstructed.scopes == frozenset({"workflow:read"})

    async def test_authenticate_service_reads_scopes(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Verify _authenticate_service parses scopes from header."""
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
                "x-tracecat-role-scopes": "workflow:read,cases:write",
            }
        )

        role = await _authenticate_service(request, api_key="test-key")

        assert role is not None
        assert role.scopes == frozenset({"workflow:read", "cases:write"})

    async def test_authenticate_service_derives_org_from_workspace(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Verify _authenticate_service derives organization_id from workspace_id when org header is missing."""
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
