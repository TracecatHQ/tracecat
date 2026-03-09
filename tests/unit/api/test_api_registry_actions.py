from __future__ import annotations

from collections.abc import Awaitable, Callable
from inspect import unwrap
from typing import cast
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from fastapi import HTTPException, status

import tracecat.registry.actions.router as registry_actions_router_module
from tracecat.auth.types import Role


@pytest.mark.anyio
async def test_delete_registry_action_is_blocked_server_side() -> None:
    action_name = "acme.test.action"
    typed_endpoint = cast(
        Callable[..., Awaitable[None]],
        unwrap(registry_actions_router_module.delete_registry_action),
    )
    role = Role(
        type="user",
        service_id="tracecat-api",
        scopes=frozenset({"org:registry:delete"}),
        organization_id=UUID("00000000-0000-0000-0000-000000000000"),
    )

    with patch.object(
        registry_actions_router_module, "RegistryActionsService"
    ) as mock_service_cls:
        with pytest.raises(HTTPException) as exc_info:
            await typed_endpoint(
                role=role,
                session=AsyncMock(),
                action_name=action_name,
            )

    assert exc_info.value.status_code == status.HTTP_409_CONFLICT
    assert exc_info.value.detail == (
        f"Action '{action_name}' cannot be deleted individually. "
        "Registry actions are served from immutable versions. "
        "Sync or promote a new registry version to remove an action."
    )
    mock_service_cls.assert_not_called()
