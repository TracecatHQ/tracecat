from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from temporalio.exceptions import ApplicationError

from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.exceptions import EntitlementRequired
from tracecat.workflow.management.definitions import resolve_registry_lock_activity
from tracecat.workflow.management.schemas import ResolveRegistryLockActivityInputs


@pytest.fixture
def mock_role() -> Role:
    return Role(
        type="service",
        service_id="tracecat-executor",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-executor"],
    )


@pytest.mark.anyio
async def test_resolve_registry_lock_activity_maps_entitlement_error(
    mock_role: Role,
) -> None:
    inputs = ResolveRegistryLockActivityInputs(
        role=mock_role,
        action_names={"tools.custom.only_action"},
    )
    mock_service = AsyncMock()
    mock_service.resolve_lock_with_bindings.side_effect = EntitlementRequired(
        "custom_registry"
    )
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_service

    with patch(
        "tracecat.workflow.management.definitions.RegistryLockService.with_session",
        return_value=mock_ctx,
    ):
        with pytest.raises(ApplicationError) as exc_info:
            await resolve_registry_lock_activity(inputs)

    app_error = exc_info.value
    assert app_error.type == "EntitlementRequired"
    assert app_error.non_retryable is True
    assert len(app_error.details) > 0
    detail = app_error.details[0]
    assert isinstance(detail, dict)
    assert detail["entitlement"] == "custom_registry"
