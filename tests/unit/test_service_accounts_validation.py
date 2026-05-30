from __future__ import annotations

import pytest
from sqlalchemy import select

from tracecat.authz.enums import ScopeSource
from tracecat.db.models import ServiceAccount, ServiceAccountApiKey
from tracecat.exceptions import TracecatValidationError
from tracecat.pagination import CursorPaginationParams
from tracecat.service_accounts.constants import (
    is_org_service_account_assignable_scope,
    is_workspace_service_account_assignable_scope,
)
from tracecat.service_accounts.service import (
    _apply_api_key_created_cursor,
    _apply_created_cursor,
)


def test_apply_created_cursor_rejects_invalid_cursor() -> None:
    with pytest.raises(
        TracecatValidationError, match="Invalid cursor for service accounts"
    ):
        _apply_created_cursor(
            select(ServiceAccount),
            params=CursorPaginationParams(limit=20, cursor="bad-cursor"),
        )


def test_apply_api_key_created_cursor_rejects_invalid_cursor() -> None:
    with pytest.raises(
        TracecatValidationError, match="Invalid cursor for service account API keys"
    ):
        _apply_api_key_created_cursor(
            select(ServiceAccountApiKey),
            params=CursorPaginationParams(limit=20, cursor="bad-cursor"),
        )


@pytest.mark.parametrize(
    "scope_name",
    [
        "workflow:read",
        "case:create",
        "case:update",
        "case:delete",
        "secret:update",
        "table:read",
        "table:create",
        "table:update",
        "table:delete",
        "workspace:member:invite",
        "agent:read",
        "agent:create",
        "agent:update",
        "agent:delete",
        "agent:execute",
        "action:tools.slack.post_message:execute",
    ],
)
def test_workspace_service_account_assignable_scope_allows_supported_api_key_scopes(
    scope_name: str,
) -> None:
    assert is_workspace_service_account_assignable_scope(
        name=scope_name,
        source=ScopeSource.PLATFORM,
        organization_id_present=False,
    )


@pytest.mark.parametrize(
    "scope_name",
    [
        "variable:read",
        "variable:update",
        "inbox:read",
        "workspace:create",
        "workspace:member:update",
        "workspace:service_account:read",
    ],
)
def test_workspace_service_account_assignable_scope_rejects_user_only_scopes(
    scope_name: str,
) -> None:
    if scope_name.startswith("variable:"):
        pytest.skip("Variable scopes are assignable to service accounts")
    assert not is_workspace_service_account_assignable_scope(
        name=scope_name,
        source=ScopeSource.PLATFORM,
        organization_id_present=False,
    )


@pytest.mark.parametrize(
    "scope_name",
    [
        "org:read",
        "org:secret:read",
        "org:secret:create",
        "org:workspace:read",
        "workspace:create",
        "workflow:update",
        "table:read",
        "action:tools.slack.post_message:execute",
    ],
)
def test_org_service_account_assignable_scope_allows_supported_api_key_scopes(
    scope_name: str,
) -> None:
    assert is_org_service_account_assignable_scope(
        name=scope_name,
        source=ScopeSource.PLATFORM,
        organization_id_present=False,
    )


@pytest.mark.parametrize(
    "scope_name",
    [
        "org:settings:read",
        "org:settings:update",
        "org:rbac:read",
        "org:registry:read",
        "org:member:invite",
        "variable:read",
        "org:service_account:read",
    ],
)
def test_org_service_account_assignable_scope_rejects_user_only_scopes(
    scope_name: str,
) -> None:
    if scope_name.startswith("variable:"):
        pytest.skip("Variable scopes are assignable to service accounts")
    assert not is_org_service_account_assignable_scope(
        name=scope_name,
        source=ScopeSource.PLATFORM,
        organization_id_present=False,
    )


@pytest.mark.parametrize(
    "scope_name",
    [
        "variable:read",
        "variable:create",
        "variable:update",
        "variable:delete",
    ],
)
def test_workspace_service_account_assignable_scope_allows_variable_scopes(
    scope_name: str,
) -> None:
    assert is_workspace_service_account_assignable_scope(
        name=scope_name,
        source=ScopeSource.PLATFORM,
        organization_id_present=False,
    )


@pytest.mark.parametrize(
    "scope_name",
    [
        "variable:read",
        "variable:create",
        "variable:update",
        "variable:delete",
    ],
)
def test_org_service_account_assignable_scope_allows_variable_scopes(
    scope_name: str,
) -> None:
    assert is_org_service_account_assignable_scope(
        name=scope_name,
        source=ScopeSource.PLATFORM,
        organization_id_present=False,
    )


def test_service_account_assignable_scope_rejects_non_platform_scopes() -> None:
    assert not is_workspace_service_account_assignable_scope(
        name="workflow:read",
        source=ScopeSource.CUSTOM,
        organization_id_present=False,
    )
    assert not is_org_service_account_assignable_scope(
        name="workflow:read",
        source=ScopeSource.PLATFORM,
        organization_id_present=True,
    )
