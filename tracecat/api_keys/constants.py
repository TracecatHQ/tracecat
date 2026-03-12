from __future__ import annotations

from enum import StrEnum

from tracecat.auth.api_keys import ORG_API_KEY_PREFIX, WORKSPACE_API_KEY_PREFIX
from tracecat.authz.enums import ScopeSource
from tracecat.authz.scopes import ADMIN_SCOPES, ORG_OWNER_SCOPES

API_KEY_HEADER_NAME = "x-tracecat-api-key"

ORG_API_KEY_MANAGEMENT_SCOPES: frozenset[str] = frozenset(
    {
        "org:api_key:read",
        "org:api_key:create",
        "org:api_key:update",
        "org:api_key:revoke",
    }
)

WORKSPACE_API_KEY_MANAGEMENT_SCOPES: frozenset[str] = frozenset(
    {
        "workspace:api_key:read",
        "workspace:api_key:create",
        "workspace:api_key:update",
        "workspace:api_key:revoke",
    }
)

ALL_API_KEY_MANAGEMENT_SCOPES = (
    ORG_API_KEY_MANAGEMENT_SCOPES | WORKSPACE_API_KEY_MANAGEMENT_SCOPES
)

WORKSPACE_API_KEY_ASSIGNABLE_SCOPES: frozenset[str] = (
    frozenset(ADMIN_SCOPES) - ALL_API_KEY_MANAGEMENT_SCOPES
)

ORG_API_KEY_ASSIGNABLE_SCOPES: frozenset[str] = (
    frozenset(ORG_OWNER_SCOPES) - ALL_API_KEY_MANAGEMENT_SCOPES
)


class APIKeyKind(StrEnum):
    ORGANIZATION = "organization"
    WORKSPACE = "workspace"


ORG_API_KEY_KIND = APIKeyKind.ORGANIZATION
WORKSPACE_API_KEY_KIND = APIKeyKind.WORKSPACE
API_KEY_KINDS = (ORG_API_KEY_KIND, WORKSPACE_API_KEY_KIND)
API_KEY_PREFIX_TO_KIND = {
    ORG_API_KEY_PREFIX: ORG_API_KEY_KIND,
    WORKSPACE_API_KEY_PREFIX: WORKSPACE_API_KEY_KIND,
}


def is_platform_action_execute_scope(name: str) -> bool:
    return name.startswith("action:") and name.endswith(":execute")


def is_org_api_key_assignable_scope(
    *,
    name: str,
    source: ScopeSource,
    organization_id_present: bool,
) -> bool:
    if organization_id_present or source != ScopeSource.PLATFORM:
        return False
    return name in ORG_API_KEY_ASSIGNABLE_SCOPES or is_platform_action_execute_scope(
        name
    )


def is_workspace_api_key_assignable_scope(
    *,
    name: str,
    source: ScopeSource,
    organization_id_present: bool,
) -> bool:
    if organization_id_present or source != ScopeSource.PLATFORM:
        return False
    return (
        name in WORKSPACE_API_KEY_ASSIGNABLE_SCOPES
        or is_platform_action_execute_scope(name)
    )
