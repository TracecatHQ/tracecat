"""Masking controls use organization and workspace settings permissions."""

from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi.testclient import TestClient

from tracecat.auth.types import Role
from tracecat.contexts import ctx_role
from tracecat.db.models import Workspace
from tracecat.settings import router as settings_router
from tracecat.workspaces import router as workspace_router


@pytest.mark.anyio
@pytest.mark.parametrize("target", ["organization", "workspace"])
@pytest.mark.parametrize("allowed", [False, True])
async def test_masking_update_requires_scope(
    client: TestClient,
    test_role: Role,
    test_workspace: Workspace,
    target: str,
    allowed: bool,
) -> None:
    scope = "org:settings:update" if target == "organization" else "workspace:update"
    role = test_role.model_copy(
        update={
            "scopes": frozenset({scope})
            if allowed
            else frozenset({"workspace:read", "org:settings:read"})
        }
    )
    token = ctx_role.set(role)
    org_service = Mock(update_app_settings=AsyncMock())
    workspace_service = Mock(
        get_workspace=AsyncMock(return_value=test_workspace),
        update_workspace=AsyncMock(return_value=test_workspace),
    )
    try:
        with (
            patch.object(settings_router, "SettingsService", return_value=org_service),
            patch.object(
                workspace_router, "WorkspaceService", return_value=workspace_service
            ),
            patch.object(
                workspace_router,
                "resolve_error_masking_mode",
                AsyncMock(return_value="conservative"),
            ),
        ):
            if target == "organization":
                response = client.patch(
                    "/settings/app", json={"app_error_masking_mode": "conservative"}
                )
            else:
                response = client.patch(
                    f"/workspaces/{test_workspace.id}",
                    json={"settings": {"error_masking_mode": "conservative"}},
                )
    finally:
        ctx_role.reset(token)
    expected_status = (204 if target == "organization" else 200) if allowed else 403
    assert response.status_code == expected_status
    update = (
        org_service.update_app_settings
        if target == "organization"
        else workspace_service.update_workspace
    )
    assert update.await_count == int(allowed)
    if allowed and target == "workspace":
        assert (
            update.call_args.kwargs["params"].settings.error_masking_mode
            == "conservative"
        )


@pytest.mark.anyio
@pytest.mark.parametrize("mode", [None, "provenance", "conservative"])
async def test_workspace_can_inherit_or_override(
    client: TestClient,
    test_admin_role: Role,
    test_workspace: Workspace,
    mode: str | None,
) -> None:
    workspace_service = Mock(
        get_workspace=AsyncMock(return_value=test_workspace),
        update_workspace=AsyncMock(return_value=test_workspace),
    )
    with (
        patch.object(
            workspace_router, "WorkspaceService", return_value=workspace_service
        ),
        patch.object(
            workspace_router,
            "resolve_error_masking_mode",
            AsyncMock(return_value="provenance"),
        ),
    ):
        response = client.patch(
            f"/workspaces/{test_workspace.id}",
            json={"settings": {"error_masking_mode": mode}},
        )
    assert response.status_code == 200
    params = workspace_service.update_workspace.call_args.kwargs["params"]
    assert params.model_dump(exclude_unset=True) == {
        "settings": {"error_masking_mode": mode}
    }
