from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from tracecat.audit.enums import AuditEventStatus
from tracecat.audit.service import AuditService
from tracecat.auth.types import Role
from tracecat.contexts import ctx_role
from tracecat.db.models import ServiceAccount, ServiceAccountApiKey
from tracecat.service_accounts.service import (
    IssuedServiceAccountApiKeyResult,
    OrganizationServiceAccountService,
    WorkspaceServiceAccountService,
)


def test_issued_service_account_api_key_result_exposes_api_key_id() -> None:
    service_account = cast(ServiceAccount, SimpleNamespace(id=uuid4()))
    api_key_id = uuid4()
    api_key = cast(ServiceAccountApiKey, SimpleNamespace(id=api_key_id))

    result = IssuedServiceAccountApiKeyResult(
        service_account=service_account,
        api_key=api_key,
        raw_key="tc_ws_sk_raw",
    )

    unpacked_service_account, unpacked_api_key, raw_key = result

    assert unpacked_service_account is service_account
    assert unpacked_api_key is api_key
    assert raw_key == "tc_ws_sk_raw"
    assert result.service_account_id == service_account.id
    assert result.api_key_id == api_key_id


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("service_cls", "scope"),
    [
        (OrganizationServiceAccountService, "org:service_account:create"),
        (WorkspaceServiceAccountService, "workspace:service_account:create"),
    ],
)
async def test_create_service_account_audit_logs_created_service_account_id(
    service_cls: type[OrganizationServiceAccountService]
    | type[WorkspaceServiceAccountService],
    scope: str,
) -> None:
    organization_id = uuid4()
    workspace_id = uuid4()
    service_account_id = uuid4()
    service_account = cast(ServiceAccount, SimpleNamespace(id=service_account_id))
    api_key = cast(ServiceAccountApiKey, SimpleNamespace(id=uuid4()))
    result = IssuedServiceAccountApiKeyResult(
        service_account=service_account,
        api_key=api_key,
        raw_key="tc_ws_sk_raw",
    )
    role = Role(
        type="user",
        user_id=uuid4(),
        organization_id=organization_id,
        workspace_id=workspace_id,
        service_id="tracecat-api",
        scopes=frozenset({scope}),
    )
    service = service_cls(AsyncMock(), role=role)
    create_event_calls: list[dict[str, object]] = []

    async def mock_create_event(*args: object, **kwargs: object) -> None:
        create_event_calls.append(kwargs)

    token = ctx_role.set(role)
    try:
        with (
            patch.object(
                service,
                "_create_service_account",
                new=AsyncMock(return_value=result),
            ),
            patch.object(AuditService, "create_event", side_effect=mock_create_event),
        ):
            await service.create_service_account(
                name="Automation",
                description=None,
                scope_ids=[],
                initial_key_name="Primary",
            )
    finally:
        ctx_role.reset(token)

    success_call = next(
        call
        for call in create_event_calls
        if call["status"] == AuditEventStatus.SUCCESS
    )
    assert success_call["resource_id"] == service_account_id
