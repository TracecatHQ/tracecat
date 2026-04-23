from __future__ import annotations

from tracecat.authz.enums import ScopeSource

API_KEY_HEADER_NAME = "x-tracecat-api-key"

WORKSPACE_SERVICE_ACCOUNT_ASSIGNABLE_SCOPES: frozenset[str] = frozenset(
    {
        "agent:read",
        "case:read",
        "case:create",
        "case:update",
        "case:delete",
        "integration:read",
        "integration:create",
        "integration:update",
        "integration:delete",
        "schedule:read",
        "schedule:create",
        "schedule:update",
        "schedule:delete",
        "secret:read",
        "secret:create",
        "secret:update",
        "secret:delete",
        "tag:read",
        "tag:create",
        "tag:update",
        "tag:delete",
        "workflow:read",
        "workflow:sync",
        "workflow:create",
        "workflow:update",
        "workflow:delete",
        "workflow:execute",
        "workflow:terminate",
        "workspace:read",
        "workspace:update",
        "workspace:delete",
        "workspace:member:read",
        "workspace:member:invite",
        "workspace:member:remove",
    }
)

ORG_SERVICE_ACCOUNT_ASSIGNABLE_SCOPES: frozenset[str] = (
    WORKSPACE_SERVICE_ACCOUNT_ASSIGNABLE_SCOPES
    | frozenset(
        {
            "org:read",
            "org:secret:read",
            "org:secret:create",
            "org:secret:update",
            "org:secret:delete",
            "org:workspace:read",
            "workspace:create",
        }
    )
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
