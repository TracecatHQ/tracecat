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

# =============================================================================
# Workspace Role Scopes
# =============================================================================

VIEWER_SCOPES: frozenset[str] = frozenset(
    {
        "workflow:read",
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
        # Billing (OWNER-only for manage)
        "org:billing:read",
        "org:billing:manage",
        # RBAC management
        "org:rbac:read",
        "org:rbac:manage",
        # Org settings management
        "org:settings:read",
        "org:settings:manage",
        # Registry management (org-level custom actions)
        "org:registry:read",
        "org:registry:manage",
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
        "workflow:read",
        "workflow:create",
        "workflow:update",
        "workflow:delete",
        "workflow:execute",
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
        # Org settings (no delete)
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
        "org:rbac:manage",
        # Org settings management
        "org:settings:read",
        "org:settings:manage",
        # Registry management (org-level custom actions)
        "org:registry:read",
        "org:registry:manage",
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
        "workflow:read",
        "workflow:create",
        "workflow:update",
        "workflow:delete",
        "workflow:execute",
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
