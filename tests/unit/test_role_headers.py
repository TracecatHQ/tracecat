"""Tests for Role header serialization and _authenticate_service() roundtrip."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from tracecat.auth.credentials import _authenticate_service
from tracecat.auth.types import Role


class MockHeaders(dict):
    """Dict subclass that can be used as request.headers mock."""


class TestRoleToHeaders:
    def test_to_headers_includes_all_fields(self) -> None:
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
        assert headers["x-tracecat-role-scopes"] == "cases:write,workflow:read"

    def test_to_headers_omits_none_fields(self) -> None:
        role = Role(
            type="service",
            service_id="tracecat-runner",
        )
        headers = role.to_headers()

        assert headers["x-tracecat-role-type"] == "service"
        assert headers["x-tracecat-role-service-id"] == "tracecat-runner"
        assert "x-tracecat-role-user-id" not in headers
        assert "x-tracecat-role-workspace-id" not in headers
        assert "x-tracecat-role-bound-workspace-id" not in headers
        assert "x-tracecat-role-organization-id" not in headers
        assert "x-tracecat-role-scopes" not in headers

    def test_to_headers_includes_service_account_metadata(self) -> None:
        service_account_id = uuid4()
        workspace_id = uuid4()
        role = Role(
            type="service_account",
            service_id="tracecat-api",
            organization_id=uuid4(),
            workspace_id=workspace_id,
            bound_workspace_id=workspace_id,
            service_account_id=service_account_id,
            scopes=frozenset({"workflow:read"}),
        )

        headers = role.to_headers()

        assert headers["x-tracecat-role-type"] == "service_account"
        assert headers["x-tracecat-role-service-id"] == "tracecat-api"
        assert headers["x-tracecat-role-service-account-id"] == str(service_account_id)
        assert headers["x-tracecat-role-bound-workspace-id"] == str(workspace_id)

    def test_service_account_role_requires_organization_id(self) -> None:
        with pytest.raises(ValidationError, match="organization_id"):
            Role(
                type="service_account",
                service_id="tracecat-api",
                service_account_id=uuid4(),
            )

    def test_service_account_role_requires_service_account_id(self) -> None:
        with pytest.raises(ValidationError, match="service_account_id"):
            Role(
                type="service_account",
                service_id="tracecat-api",
                organization_id=uuid4(),
            )

    def test_service_account_role_rejects_user_id(self) -> None:
        with pytest.raises(ValidationError, match="must not set user_id"):
            Role(
                type="service_account",
                service_id="tracecat-api",
                organization_id=uuid4(),
                service_account_id=uuid4(),
                user_id=uuid4(),
            )

    def test_service_account_role_rejects_mismatched_bound_workspace(self) -> None:
        with pytest.raises(ValidationError, match="bound_workspace_id"):
            Role(
                type="service_account",
                service_id="tracecat-api",
                organization_id=uuid4(),
                workspace_id=uuid4(),
                bound_workspace_id=uuid4(),
                service_account_id=uuid4(),
            )


@pytest.mark.anyio
class TestAuthenticateServiceRoundtrip:
    async def test_roundtrip_preserves_all_fields(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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
    ) -> None:
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
    ) -> None:
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

    async def test_authenticate_service_reads_workspace_provenance(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "tracecat.auth.credentials.config.TRACECAT__SERVICE_KEY", "test-key"
        )
        monkeypatch.setattr(
            "tracecat.auth.credentials.config.TRACECAT__SERVICE_ROLES_WHITELIST",
            ["tracecat-runner"],
        )

        workspace_id = uuid4()
        request = MagicMock()
        request.headers = MockHeaders(
            {
                "x-tracecat-role-service-id": "tracecat-runner",
                "x-tracecat-role-workspace-id": str(workspace_id),
                "x-tracecat-role-bound-workspace-id": str(workspace_id),
            }
        )

        role = await _authenticate_service(request, api_key="test-key")

        assert role is not None
        assert role.workspace_id == workspace_id
        assert role.bound_workspace_id == workspace_id
