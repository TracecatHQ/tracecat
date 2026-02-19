"""RBAC scope definitions for Tracecat.

This module defines the default scope sets for preset roles.

Scopes follow the OAuth 2.0 format: `{resource}:{action}`

Standard actions (ordered by privilege):
- read: View/list resources
- create: Create new resources
- update: Modify existing resources
- delete: Remove resources
- execute: Run/trigger resources
"""

from __future__ import annotations

from typing import cast, get_args

from tracecat.identifiers import InternalServiceID

# =============================================================================
# Workspace Role Scopes
# =============================================================================

VIEWER_SCOPES: frozenset[str] = frozenset(
    {
        "inbox:read",
        "workflow:read",
        "integration:read",
        "case:read",
        "table:read",
        "schedule:read",
        "agent:read",
        "secret:read",
        "tag:read",
        "variable:read",
        "workspace:read",
        "workspace:member:read",
    }
)

EDITOR_SCOPES: frozenset[str] = VIEWER_SCOPES | frozenset(
    {
        "workflow:create",
        "workflow:update",
        "workflow:execute",
        "workflow:terminate",
        "integration:create",
        "integration:update",
        "case:create",
        "case:update",
        "table:create",
        "table:update",
        "schedule:create",
        "schedule:update",
        "agent:execute",
        "secret:create",
        "secret:update",
        "tag:create",
        "tag:update",
        "variable:create",
        "variable:update",
        # Core actions available to editors
        "action:core.*:execute",
    }
)

ADMIN_SCOPES: frozenset[str] = EDITOR_SCOPES | frozenset(
    {
        "workflow:delete",
        "case:delete",
        "table:delete",
        "schedule:delete",
        "secret:delete",
        "tag:delete",
        "variable:delete",
        "agent:create",
        "agent:update",
        "agent:delete",
        "integration:delete",
        "workspace:update",
        "workspace:delete",
        "workspace:member:invite",
        "workspace:member:remove",
        "workspace:member:update",
        # Full action execution
        "action:*:execute",
    }
)

# =============================================================================
# Organization Role Scopes
# =============================================================================

# Org OWNER/ADMIN roles grant implicit access to ALL workspaces in the org.
# These scopes define WHAT they can do, not WHERE.

ORG_OWNER_SCOPES: frozenset[str] = frozenset(
    {
        # Org settings
        "org:read",
        "org:update",
        "org:delete",  # OWNER-only
        # Org member management
        "org:member:read",
        "org:member:invite",
        "org:member:remove",
        "org:member:update",
        # Billing (OWNER-only for updates)
        "org:billing:read",
        "org:billing:update",
        # RBAC management
        "org:rbac:read",
        "org:rbac:create",
        "org:rbac:update",
        "org:rbac:delete",
        # Org settings management
        "org:settings:read",
        "org:settings:update",
        "org:settings:delete",
        # Registry management (org-level custom actions)
        "org:registry:read",
        "org:registry:create",
        "org:registry:update",
        "org:registry:delete",
        # Full workspace control across the org
        "workspace:read",
        "workspace:create",
        "workspace:update",
        "workspace:delete",
        "workspace:member:read",
        "workspace:member:invite",
        "workspace:member:remove",
        "workspace:member:update",
        # Full resource control
        "inbox:read",
        "workflow:read",
        "workflow:create",
        "workflow:update",
        "workflow:delete",
        "workflow:execute",
        "workflow:terminate",
        "integration:read",
        "integration:create",
        "integration:update",
        "integration:delete",
        "case:read",
        "case:create",
        "case:update",
        "case:delete",
        "table:read",
        "table:create",
        "table:update",
        "table:delete",
        "tag:read",
        "tag:create",
        "tag:update",
        "tag:delete",
        "variable:read",
        "variable:create",
        "variable:update",
        "variable:delete",
        "schedule:read",
        "schedule:create",
        "schedule:update",
        "schedule:delete",
        "agent:read",
        "agent:create",
        "agent:update",
        "agent:delete",
        "agent:execute",
        "secret:read",
        "secret:create",
        "secret:update",
        "secret:delete",
        # Organization secrets (org-scoped, not workspace-scoped)
        "org:secret:read",
        "org:secret:create",
        "org:secret:update",
        "org:secret:delete",
        # Full action execution
        "action:*:execute",
    }
)

ORG_ADMIN_SCOPES: frozenset[str] = frozenset(
    {
        # Org settings (no org delete)
        "org:read",
        "org:update",
        # Org member management
        "org:member:read",
        "org:member:invite",
        "org:member:remove",
        "org:member:update",
        # Billing (read only for admin)
        "org:billing:read",
        # RBAC management
        "org:rbac:read",
        "org:rbac:create",
        "org:rbac:update",
        "org:rbac:delete",
        # Org settings management
        "org:settings:read",
        "org:settings:update",
        "org:settings:delete",
        # Registry management (org-level custom actions)
        "org:registry:read",
        "org:registry:create",
        "org:registry:update",
        "org:registry:delete",
        # Full workspace control across the org
        "workspace:read",
        "workspace:create",
        "workspace:update",
        "workspace:delete",
        "workspace:member:read",
        "workspace:member:invite",
        "workspace:member:remove",
        "workspace:member:update",
        # Full resource control
        "inbox:read",
        "workflow:read",
        "workflow:create",
        "workflow:update",
        "workflow:delete",
        "workflow:execute",
        "workflow:terminate",
        "integration:read",
        "integration:create",
        "integration:update",
        "integration:delete",
        "case:read",
        "case:create",
        "case:update",
        "case:delete",
        "table:read",
        "table:create",
        "table:update",
        "table:delete",
        "tag:read",
        "tag:create",
        "tag:update",
        "tag:delete",
        "variable:read",
        "variable:create",
        "variable:update",
        "variable:delete",
        "schedule:read",
        "schedule:create",
        "schedule:update",
        "schedule:delete",
        "agent:read",
        "agent:create",
        "agent:update",
        "agent:delete",
        "agent:execute",
        "secret:read",
        "secret:create",
        "secret:update",
        "secret:delete",
        # Organization secrets (org-scoped, not workspace-scoped)
        "org:secret:read",
        "org:secret:create",
        "org:secret:update",
        "org:secret:delete",
        # Full action execution
        "action:*:execute",
    }
)

ORG_MEMBER_SCOPES: frozenset[str] = frozenset(
    {
        # Baseline org access; workspace/resource access requires workspace membership
        "org:read",
        "org:member:read",
    }
)

# =============================================================================
# Preset Role -> Scope Set Mapping
# =============================================================================

PRESET_ROLE_SCOPES: dict[str, frozenset[str]] = {
    # Workspace roles
    "workspace-viewer": VIEWER_SCOPES,
    "workspace-editor": EDITOR_SCOPES,
    "workspace-admin": ADMIN_SCOPES,
    # Organization roles
    "organization-owner": ORG_OWNER_SCOPES,
    "organization-admin": ORG_ADMIN_SCOPES,
    "organization-member": ORG_MEMBER_SCOPES,
}

# =============================================================================
# Service Principal Scope Allowlist
# =============================================================================

WORKSPACE_OPERATIONAL_SCOPES: frozenset[str] = frozenset(
    {
        "inbox:read",
        "workflow:read",
        "workflow:create",
        "workflow:update",
        "workflow:delete",
        "workflow:execute",
        "workflow:terminate",
        "integration:read",
        "integration:create",
        "integration:update",
        "integration:delete",
        "case:read",
        "case:create",
        "case:update",
        "case:delete",
        "table:read",
        "table:create",
        "table:update",
        "table:delete",
        "variable:read",
        "variable:create",
        "variable:update",
        "variable:delete",
        "secret:read",
        "secret:create",
        "secret:update",
        "secret:delete",
        "agent:read",
        "agent:create",
        "agent:update",
        "agent:delete",
        "agent:execute",
        "schedule:read",
        "schedule:create",
        "schedule:update",
        "schedule:delete",
        "tag:read",
        "tag:create",
        "tag:update",
        "tag:delete",
        "workspace:read",
        "workspace:create",
        "workspace:delete",
        "workspace:member:read",
        "action:*:execute",
    }
)

# Grant baseline operational scopes to all known internal service IDs.
# Fine-grained admission is still enforced by service-role authentication.
SERVICE_PRINCIPAL_SCOPES: dict[InternalServiceID, frozenset[str]] = {
    cast(InternalServiceID, service_id): WORKSPACE_OPERATIONAL_SCOPES
    for service_id in get_args(InternalServiceID)
}

# tracecat-service needs org-scoped secret reads for registry sync SSH key lookup.
SERVICE_PRINCIPAL_SCOPES["tracecat-service"] = SERVICE_PRINCIPAL_SCOPES[
    "tracecat-service"
] | frozenset({"org:secret:read"})
