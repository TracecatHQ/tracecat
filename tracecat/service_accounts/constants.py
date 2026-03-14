from __future__ import annotations

from tracecat.authz.enums import ScopeSource
from tracecat.authz.scopes import ADMIN_SCOPES, ORG_OWNER_SCOPES

API_KEY_HEADER_NAME = "x-tracecat-api-key"

ORG_SERVICE_ACCOUNT_MANAGEMENT_SCOPES: frozenset[str] = frozenset(
    {
        "org:service_account:read",
        "org:service_account:create",
        "org:service_account:update",
        "org:service_account:disable",
    }
)

WORKSPACE_SERVICE_ACCOUNT_MANAGEMENT_SCOPES: frozenset[str] = frozenset(
    {
        "workspace:service_account:read",
        "workspace:service_account:create",
        "workspace:service_account:update",
        "workspace:service_account:disable",
    }
)

ALL_SERVICE_ACCOUNT_MANAGEMENT_SCOPES = (
    ORG_SERVICE_ACCOUNT_MANAGEMENT_SCOPES | WORKSPACE_SERVICE_ACCOUNT_MANAGEMENT_SCOPES
)

WORKSPACE_SERVICE_ACCOUNT_ASSIGNABLE_SCOPES: frozenset[str] = (
    frozenset(ADMIN_SCOPES) - ALL_SERVICE_ACCOUNT_MANAGEMENT_SCOPES
)

ORG_SERVICE_ACCOUNT_ASSIGNABLE_SCOPES: frozenset[str] = (
    frozenset(ORG_OWNER_SCOPES) - ALL_SERVICE_ACCOUNT_MANAGEMENT_SCOPES
)


def is_platform_action_execute_scope(name: str) -> bool:
    return name.startswith("action:") and name.endswith(":execute")


def is_org_service_account_assignable_scope(
    *,
    name: str,
    source: ScopeSource,
    organization_id_present: bool,
) -> bool:
    if organization_id_present or source != ScopeSource.PLATFORM:
        return False
    return (
        name in ORG_SERVICE_ACCOUNT_ASSIGNABLE_SCOPES
        or is_platform_action_execute_scope(name)
    )


def is_workspace_service_account_assignable_scope(
    *,
    name: str,
    source: ScopeSource,
    organization_id_present: bool,
) -> bool:
    if organization_id_present or source != ScopeSource.PLATFORM:
        return False
    return (
        name in WORKSPACE_SERVICE_ACCOUNT_ASSIGNABLE_SCOPES
        or is_platform_action_execute_scope(name)
    )
