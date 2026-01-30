# RBAC Implementation Plan

## Current State Analysis

### Problems with Existing Model

1. **Three overlapping "admin" concepts:**
   - `User.role = UserRole.ADMIN` → grants `AccessLevel.ADMIN` globally
   - `OrganizationMembership.role = OrgRole.ADMIN` → org-level management
   - `Membership.role = WorkspaceRole.ADMIN` → workspace-level management
   - `User.is_superuser = True` → platform superuser

2. **`User.role` vs `User.is_superuser` redundancy:**
   - Both can grant `AccessLevel.ADMIN`
   - Unclear semantic difference

3. **Implicit org admin workspace access:**
   - Org admins bypass workspace membership checks via `AccessLevel.ADMIN`
   - No explicit membership record created
   - `workspace_role` is `None` for org admins
   - Fragile - code may assume `workspace_role` is always set

4. **Hardcoded admin checks:**
   - `@require_org_admin` decorators prevent flexible custom roles
   - Can't create "People Manager" role that can invite but not delete

---

## Proposed Model: Everything is a Scope

### Core Principle

**Tiers determine access boundaries (who can enter a container).**
**Scopes determine capabilities (what you can do once inside).**

All capabilities—including administrative ones—are scopes. No hardcoded tier checks for capabilities.

### Security Principles (Non-Negotiable)

1. **Default deny**: if a scope check is missing, access is denied.
2. **Least privilege**: avoid global `*` outside platform superusers; prefer explicit scope sets and narrow wildcards like `workflow:*`.
3. **Tenant boundary enforcement (IDOR prevention)**: scope checks do not replace org/workspace scoping in DB queries.
4. **No privilege escalation via RBAC UI**: RBAC management endpoints require dedicated RBAC management scopes (not `org:update`).

### Three-Tier Hierarchy

```
┌─────────────────────────────────────────────────────────────────┐
│ PLATFORM TIER                                                    │
│ - User.is_superuser = True                                       │
│ - Can access ANY org/workspace                                   │
│ - Implicit "*" scope (all capabilities)                          │
│ - Invisible to orgs (unless explicitly added as member)          │
│ - Protected: org admins cannot modify superusers                 │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ ORGANIZATION TIER                                                │
│ - OrganizationMembership.role = OWNER | ADMIN | MEMBER           │
│ - OWNER/ADMIN: Can access any workspace in org                   │
│ - OWNER/ADMIN scopes are explicit (no global "*")                │
│ - MEMBER: Must have explicit workspace membership                │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ WORKSPACE TIER                                                   │
│ - Membership.role = ADMIN | EDITOR | VIEWER                      │
│ - Scopes determined by workspace role + custom role assignments  │
└─────────────────────────────────────────────────────────────────┘
```

### Scope Resolution

| User Type | Access | Scopes |
|-----------|--------|--------|
| Platform Superuser | Any org, any workspace | `*` (all) |
| Org Owner/Admin | Their org, any workspace in org | Explicit org-role scopes + group scopes |
| Org Member | Their org, only explicit workspace memberships | Workspace membership scopes + group scopes |
| Workspace Member | Their workspace(s) | Per workspace role + custom roles |

---

## Scope Design (OAuth 2.0 Compliant)

### Format

```
{resource}:{action}
```

**Standard Actions** (ordered by privilege):
- `read` - View/list resources
- `create` - Create new resources
- `update` - Modify existing resources
- `delete` - Remove resources
- `execute` - Run/trigger resources

### Scope Categories

#### 0. RBAC Administration Scopes (New)

These protect the RBAC control plane (roles, scopes, groups, assignments). Do **not** gate these with `org:update`.

```
org:rbac:read           # View roles/scopes/groups/assignments
org:rbac:manage         # Create/update/delete roles/scopes/groups + manage assignments
```

RBAC management must be **non-escalating**:

- A user can only create/update roles with scopes they are allowed to grant.
- A user can only assign roles (via group assignment) whose scopes are within their grantable set.
- Default grant rule (minimal/secure): `grantable_scopes == effective_scopes` (platform superuser has `*` and can grant anything).

#### 1. Org-Level Scopes

```
org:read                # View org settings
org:update              # Modify org settings
org:delete              # Delete org

org:member:read         # List org members
org:member:invite       # Invite users to org
org:member:remove       # Remove users from org
org:member:update       # Change member org roles

org:billing:read        # View billing info
org:billing:manage      # Manage billing
```

### Default Org Role Scopes (Proposed)

```python
ORG_ROLE_SCOPES: dict[OrgRole, set[str]] = {
    # Full org control + full workspace/resource control across the org.
    OrgRole.OWNER: {
        "org:read",
        "org:update",
        "org:delete",  # OWNER-only
        "org:member:*",
        "org:billing:*",  # OWNER-only manage
        "org:rbac:*",
        "workspace:*",
        "workspace:member:*",
        "workflow:*",
        "case:*",
        "table:*",
        "schedule:*",
        "agent:*",
        "secret:*",
        "action:*:execute",
    },
    # Same as OWNER minus org deletion and billing management.
    OrgRole.ADMIN: {
        "org:read",
        "org:update",
        "org:member:*",
        "org:billing:read",
        "org:rbac:*",
        "workspace:*",
        "workspace:member:*",
        "workflow:*",
        "case:*",
        "table:*",
        "schedule:*",
        "agent:*",
        "secret:*",
        "action:*:execute",
    },
    # Baseline org access; workspace/resource access still requires workspace membership.
    OrgRole.MEMBER: {
        "org:read",
        "org:member:read",
    },
}
```

#### 2. Workspace-Level Scopes

```
workspace:read          # View workspace settings
workspace:update        # Modify workspace settings
workspace:delete        # Delete workspace

workspace:member:read   # List workspace members
workspace:member:invite # Add users to workspace
workspace:member:remove # Remove users from workspace
workspace:member:update # Change member workspace roles
```

#### 3. Resource Scopes

```
workflow:read, workflow:create, workflow:update, workflow:delete, workflow:execute
case:read, case:create, case:update, case:delete
secret:read, secret:create, secret:update, secret:delete
table:read, table:create, table:update, table:delete
schedule:read, schedule:create, schedule:update, schedule:delete
agent:read, agent:create, agent:update, agent:delete, agent:execute
```

#### 4. Registry Action Scopes (Dynamic)

Auto-generated from loaded registry actions:

```
action:core.http_request:execute
action:tools.zendesk.create_ticket:execute
action:tools.okta.list_users:execute
action:tools.virustotal.*:execute   # Wildcard for namespace
```

**Generation:** On registry sync, create scope for each action.

#### 5. Custom Resource Scopes (User-defined)

For restricting specific resources:

```
workflow:{workflow_id}:execute
agent:{agent_preset_id}:execute
```

---

## Scope Lifecycle (Seeding + Sync)

### Sources

- `system`: built-in platform scopes (org/workspace/resources/RBAC admin).
- `registry`: derived from registry actions (platform actions + org-installed custom actions).
- `custom`: user-created scopes (usually wildcards/convenience patterns).

### Seeding Strategy (Idempotent Upserts)

1. **On startup**:
   - Upsert all `system` scopes (`organization_id = NULL`).
   - Ensure each org has system roles (Viewer/Editor/Admin) in the `role` table, wired to the correct scopes.

2. **On registry sync**:
   - For every action key, ensure the canonical execute scope exists:
     - `action:{action_key}:execute`
     - If the action is org-scoped, set `scope.organization_id = <org_id>`; if global/system registry, keep `organization_id = NULL`.
   - If an action declares additional scopes (see below), ensure they exist too.
   - Do **not** automatically delete missing registry scopes (roles may reference them); optionally hide/deprecate in UI later.

### Custom Actions: "Bring Your Own Scopes"

Custom actions naturally bring a new permission boundary: the action itself.

- Every custom action key results in a new scope string: `action:{action_key}:execute`.
- These scopes are created automatically during registry sync (`source='registry'`).

If action authors need extra granularity, prefer defining multiple actions (each gets its own scope) over adding multiple custom scope concepts.

### Optional: Action-Declared Extra Scopes (Coupled to Registry)

To support org-specific permission models while staying safe by default, registry actions may declare **additional** required scopes.

Example action metadata (illustrative):

```yaml
authz:
  # Always enforced by the platform for this action (cannot be removed):
  # - action:{action_key}:execute
  required_scopes:
    - secret:read
  declare_scopes:
    - action:tools.my_company.*:execute
```

Rules:

- The platform always enforces `action:{action_key}:execute` for every action step (API + worker).
- `required_scopes` can only add requirements, never remove the canonical action execute scope.
- `declare_scopes` is optional convenience for seeding scope strings that admins can grant (typically wildcard patterns).
- Prevent namespace squatting: registry-declared scopes must not shadow reserved system prefixes (e.g., `org:*`, `workspace:*`) unless they exactly match an existing global system scope.

### Custom Scopes (UI/API)

`POST /scopes` exists primarily for convenience/wildcard scopes and future object-level scopes.
It should be safe to run without touching code: creating a scope does nothing until it is referenced by:

- roles/groups (grant), and
- an enforcement point (API endpoint / worker / service) that checks it.

## Custom Role Examples

**"People Manager"** - can invite but no other admin capabilities:
```python
{
    "org:member:read",
    "org:member:invite",
    "workspace:member:read",
    "workspace:member:invite",
}
```

**"Security Analyst"** - run workflows, manage cases, use specific tools:
```python
{
    "workflow:read",
    "workflow:execute",
    "case:read",
    "case:create",
    "case:update",
    "action:tools.virustotal.*:execute",
    "action:tools.shodan.*:execute",
}
```

**"Workspace Admin"** - full workspace control, no org access:
```python
{
    "workspace:*",
    "workflow:*",
    "case:*",
    "table:*",
    "secret:*",
    "schedule:*",
    "agent:*",
}
```

**"Viewer"** (system role) - read-only:
```python
{
    "workflow:read",
    "case:read",
    "table:read",
    "schedule:read",
    "agent:read",
    "workspace:member:read",
}
```

**"Editor"** (system role) - create/edit, no delete or admin:
```python
VIEWER_SCOPES | {
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
}
```

**"Admin"** (system role) - full workspace capabilities:
```python
EDITOR_SCOPES | {
    "workflow:delete",
    "case:delete",
    "table:delete",
    "schedule:delete",
    "secret:*",
    "agent:create",
    "agent:update",
    "agent:delete",
    "workspace:*",
}
```

---

## Auth Layer

### Single Decorator: `@require_scope`

All capability checks use one decorator:

```python
def require_scope(*scopes: str, require_all: bool = True):
    """Require specific scopes to access endpoint."""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            user_scopes = ctx_scopes.get()

            # "*" scope means all access (platform superuser only)
            if "*" in user_scopes:
                return await func(*args, **kwargs)

            required = set(scopes)
            if require_all:
                if not required.issubset(user_scopes):
                    missing = required - user_scopes
                    raise ScopeDeniedError(missing_scopes=list(missing))
            else:
                if not required.intersection(user_scopes):
                    raise ScopeDeniedError(required_scopes=list(scopes))

            return await func(*args, **kwargs)
        return wrapper
    return decorator
```

### Graceful 403 (In-Tenant Permission Denied)

- **Cross-tenant IDs**: return **404** by scoping DB queries (IDOR prevention).
- **In-tenant missing scope**: return **403** with a stable, machine-readable payload.

Suggested response shape:

```json
{
  "error": {
    "code": "insufficient_scope",
    "message": "You don't have permission to perform this action in this workspace.",
    "required_scopes": ["workflow:execute"],
    "missing_scopes": ["workflow:execute"]
  }
}
```

If using bearer tokens, consider also returning `WWW-Authenticate` with `error=\"insufficient_scope\"` and `scope=\"...\"`.

### Context Handling via RoleACL (Existing)

`RoleACL` already handles tier/access logic:

```python
RoleACL(require_workspace="yes")      # Workspace endpoints - validates membership or admin
RoleACL(require_workspace="no")       # Org-level endpoints
RoleACL(require_workspace="optional") # Either
```

Workspace/container access bypass logic stays in `RoleACL` (platform superuser + org OWNER/ADMIN). Scope checks remain separate.

Group assignments **do not grant container access** (org membership/workspace membership rules remain unchanged); they only contribute scopes once `RoleACL` succeeds.

### Scope Computation in `_role_dependency()`

```python
# After building Role object...

scopes: frozenset[str] = frozenset()

if role.is_platform_superuser:
    scopes = frozenset({"*"})
else:
    scope_set: set[str] = set()

    # Explicit scopes from org tier role (OWNER/ADMIN/MEMBER)
    scope_set |= ORG_ROLE_SCOPES.get(role.org_role, set())

    # Org-wide group assignments (workspace_id is NULL)
    scope_set |= await rbac_service.get_group_scopes(
        user_id=role.user_id,
        workspace_id=None,
    )

    if role.workspace_id:
        # Base scopes from workspace membership role (system role)
        if role.workspace_role:
            scope_set |= SYSTEM_ROLE_SCOPES[role.workspace_role]

        # Workspace-specific group assignments
        scope_set |= await rbac_service.get_group_scopes(
            user_id=role.user_id,
            workspace_id=role.workspace_id,
        )

    scopes = frozenset(scope_set)

ctx_scopes.set(scopes)
```

### Wildcards / Matching Semantics (Decide + Implement Early)

Wildcards are required (`workflow:*`, `action:tools.virustotal.*:execute`), but matching must be safe and deterministic.

Proposed minimal rules:

- **Required scopes are exact strings** (no wildcards in `@require_scope(...)`).
- **Granted scopes may include wildcards** (from system roles, org roles, registry convenience scopes, custom roles).
- Use **`fnmatchcase`-style matching**, not regex. Avoid regex-based user input matching.
- Validate scope strings on write:
  - lowercase + `[a-z0-9:_.*.-]` only
  - only allow `*` (no `?` / `[]` patterns)
  - optionally restrict `*` to whole-segment wildcards (e.g., `workflow:*`, `action:tools.virustotal.*:execute`)

### Tenant Boundaries / IDOR Prevention (Must-Have)

Scope checks do not prevent cross-tenant access by ID. Every endpoint/service must enforce container ownership in queries.

Rules:

- For **workspace endpoints**: every resource query includes `WHERE workspace_id = :role.workspace_id` (or joins through a workspace-owned parent).
- For **org endpoints**: every resource query includes `WHERE organization_id = :role.organization_id`.
- Avoid `get_by_id(id)` patterns; use `get_in_workspace(workspace_id, id)` / `get_in_org(org_id, id)` helpers.
- Add regression tests: same-scope user in workspace A cannot access resource ID from workspace B (expect 404).

### Runtime Authorization (Workflow/Agent Execution)

API endpoint scopes are insufficient if execution happens in workers (Temporal) without re-checking authorization.

Minimum requirements:

- Propagate an **auth context** into execution jobs: `user_id`, `organization_id`, `workspace_id`, `effective_scopes` (or a reference to compute them server-side).
- Before running a registry action step, enforce `action:{action_key}:execute` (or matching wildcard).
- Before reading/updating secrets during execution, enforce `secret:read`/`secret:update` **and** workspace ownership of the secret (IDOR).
- Scheduled runs / agent runs should execute under an explicit identity (service account/token) with an explicit scope set.

### Endpoint Examples

```python
# Resource endpoint - just check scope
@router.post("/workflows")
@require_scope("workflow:create")
async def create_workflow(role: Role = RoleACL(require_workspace="yes")):
    ...

# Org member management - scope, not tier check
@router.post("/org/members/invite")
@require_scope("org:member:invite")
async def invite_member(role: Role = RoleACL(require_workspace="no")):
    ...

# Dangerous operation - specific scope
@router.delete("/org")
@require_scope("org:delete")
async def delete_org(role: Role = RoleACL(require_workspace="no")):
    ...

# Multiple scopes required
@router.post("/workflows/{workflow_id}/execute")
@require_scope("workflow:read", "workflow:execute")
async def execute_workflow(role: Role = RoleACL(require_workspace="yes")):
    ...
```

---

## Database Schema

### New Tables

```sql
-- Scope definitions
CREATE TABLE scope (
    id UUID PRIMARY KEY,
    name VARCHAR NOT NULL,            -- "workflow:read", "org:member:invite"
    resource VARCHAR NOT NULL,         -- "workflow", "org:member"
    action VARCHAR NOT NULL,           -- "read", "invite"
    description VARCHAR,
    source VARCHAR NOT NULL,           -- 'system', 'registry', 'custom'
    source_ref VARCHAR,                -- registry action key or resource id
    organization_id UUID REFERENCES organization(id), -- NULL for system/registry scopes
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(organization_id, name)
);

-- Postgres: system/registry scopes are global; enforce uniqueness for organization_id IS NULL
-- CREATE UNIQUE INDEX scope_system_name_unique ON scope (name) WHERE organization_id IS NULL;

-- Roles (org-scoped)
CREATE TABLE role (
    id UUID PRIMARY KEY,
    name VARCHAR NOT NULL,
    description VARCHAR,
    organization_id UUID NOT NULL REFERENCES organization(id),
    is_system BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    created_by UUID REFERENCES "user"(id),
    UNIQUE(organization_id, name)
);

-- Role → Scope junction
CREATE TABLE role_scope (
    role_id UUID NOT NULL REFERENCES role(id) ON DELETE CASCADE,
    scope_id UUID NOT NULL REFERENCES scope(id) ON DELETE CASCADE,
    PRIMARY KEY (role_id, scope_id)
);

-- Groups (org-scoped)
CREATE TABLE group (
    id UUID PRIMARY KEY,
    name VARCHAR NOT NULL,
    description VARCHAR,
    organization_id UUID NOT NULL REFERENCES organization(id),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    created_by UUID REFERENCES "user"(id),
    UNIQUE(organization_id, name)
);

-- Group membership
CREATE TABLE group_member (
    user_id UUID NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
    group_id UUID NOT NULL REFERENCES group(id) ON DELETE CASCADE,
    added_at TIMESTAMP DEFAULT NOW(),
    added_by UUID REFERENCES "user"(id),
    PRIMARY KEY (user_id, group_id)
);

-- Group → (Org or Workspace) → Role assignment
CREATE TABLE group_assignment (
    id UUID PRIMARY KEY,
    group_id UUID NOT NULL REFERENCES group(id) ON DELETE CASCADE,
    workspace_id UUID REFERENCES workspace(id) ON DELETE CASCADE, -- NULL => org-wide assignment
    role_id UUID NOT NULL REFERENCES role(id),
    assigned_at TIMESTAMP DEFAULT NOW(),
    assigned_by UUID REFERENCES "user"(id),
    UNIQUE(group_id, workspace_id)
);

-- Postgres: enforce a single org-wide assignment per group
-- CREATE UNIQUE INDEX group_assignment_org_unique ON group_assignment (group_id) WHERE workspace_id IS NULL;
```

### Modifications

1. Add `VIEWER` to `WorkspaceRole` enum
2. Add `created_at`, `updated_at` to `Membership` table
3. Deprecate `User.role` column (keep for backwards compat, ignore in auth)

---

## API Endpoints

### Scopes API (`/api/scopes`)

| Method | Endpoint | Description | Scope Required |
|--------|----------|-------------|----------------|
| GET | `/scopes` | List all scopes | `org:rbac:read` |
| POST | `/scopes` | Create custom scope | `org:rbac:manage` |
| DELETE | `/scopes/{scope_id}` | Delete custom scope | `org:rbac:manage` |

### Roles API (`/api/roles`)

| Method | Endpoint | Description | Scope Required |
|--------|----------|-------------|----------------|
| GET | `/roles` | List org roles | `org:rbac:read` |
| POST | `/roles` | Create custom role | `org:rbac:manage` |
| GET | `/roles/{role_id}` | Get role with scopes | `org:rbac:read` |
| PATCH | `/roles/{role_id}` | Update role | `org:rbac:manage` |
| DELETE | `/roles/{role_id}` | Delete role | `org:rbac:manage` |

### Groups API (`/api/groups`)

| Method | Endpoint | Description | Scope Required |
|--------|----------|-------------|----------------|
| GET | `/groups` | List org groups | `org:rbac:read` |
| POST | `/groups` | Create group | `org:rbac:manage` |
| GET | `/groups/{group_id}` | Get group | `org:rbac:read` |
| PATCH | `/groups/{group_id}` | Update group | `org:rbac:manage` |
| DELETE | `/groups/{group_id}` | Delete group | `org:rbac:manage` |
| POST | `/groups/{group_id}/members` | Add member | `org:member:update` |
| DELETE | `/groups/{group_id}/members/{user_id}` | Remove member | `org:member:update` |

### Workspace Group Assignments (`/api/workspaces/{workspace_id}/groups`)

| Method | Endpoint | Description | Scope Required |
|--------|----------|-------------|----------------|
| GET | `/groups` | List assigned groups | `workspace:member:read` |
| POST | `/groups` | Assign group with role | `workspace:member:update` |
| PATCH | `/groups/{group_id}` | Change role | `workspace:member:update` |
| DELETE | `/groups/{group_id}` | Remove assignment | `workspace:member:update` |

### Org Group Assignments (`/api/org/groups/{group_id}/role`)

Assign an org-wide role to a group (implemented as `group_assignment.workspace_id = NULL`).

| Method | Endpoint | Description | Scope Required |
|--------|----------|-------------|----------------|
| PUT | `/org/groups/{group_id}/role` | Set org-wide role for group | `org:rbac:manage` |
| DELETE | `/org/groups/{group_id}/role` | Remove org-wide role | `org:rbac:manage` |

### User Scopes (`/api/users/me/scopes`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/users/me/scopes` | Get effective scopes for current org/workspace (explicit selector required) |

---

## Frontend

### Scope Provider

```tsx
interface ScopeContextValue {
  scopes: Set<string>
  isLoading: boolean
  hasScope(scope: string): boolean
  hasAnyScope(scopes: string[]): boolean
  hasAllScopes(scopes: string[]): boolean
}
```

### Scope Guard

```tsx
<ScopeGuard scope="workflow:create" fallback={<Disabled />}>
  <CreateWorkflowButton />
</ScopeGuard>

<ScopeGuard scope="org:member:invite" fallback={null}>
  <InviteMemberButton />
</ScopeGuard>

<ScopeGuard scopes={["workflow:read", "workflow:execute"]} all fallback={<Disabled />}>
  <RunWorkflowButton />
</ScopeGuard>
```

### Admin UI Pages

- **Org Settings > Roles**: Create/edit custom roles with scope picker
- **Org Settings > Groups**: Manage groups and members
- **Org Settings > Scopes**: View system scopes, create custom scopes
- **Workspace Settings > Access**: Assign groups to workspace with roles

---

## Implementation Order (PR Stack)

> **Note:** Create as a Graphite PR stack for end-to-end testing. Each PR builds on the previous.
> Implementing agent must create each PR in the stack sequentially.

### PR 1: Database Foundation
**Branch:** `feat/rbac-schema`

- Alembic migration for new tables:
  - `scope`
  - `role`
  - `role_scope`
  - `group`
  - `group_member`
  - `group_assignment`
- SQLAlchemy models in `tracecat/db/models.py`
- Add `VIEWER` to `WorkspaceRole` enum

---

### PR 2: Core Auth Layer
**Branch:** `feat/rbac-auth-layer`

- Add `ctx_scopes` to `tracecat/contexts.py`
- Define `SYSTEM_ROLE_SCOPES` mapping (Viewer/Editor/Admin → scope sets)
- Define `ORG_ROLE_SCOPES` mapping (Owner/Admin/Member → scope sets; no global `*`)
- Implement `@require_scope` decorator in `tracecat/authz/controls.py`
- Implement wildcard matching (restricted `*`, `fnmatchcase`-style) inside `require_scope`
- Add `ScopeDeniedError` exception
- Add scope computation in `_role_dependency()` (credentials.py)
- Add exception handler for `ScopeDeniedError` → 403

---

### PR 3: Scope Seeding
**Branch:** `feat/rbac-scope-seeding`

- System scope definitions (org, workspace, resource scopes, RBAC admin scopes)
- Seeding logic on startup
- Registry action scope generation (after registry sync)
- System role seeding per org (Viewer/Editor/Admin with scopes)

---

### PR 4: RBAC Service & APIs
**Branch:** `feat/rbac-apis`

- `RBACService` for scope computation (supports org-wide + workspace-specific group assignments)
- Scopes API (`/api/scopes`)
- Roles API (`/api/roles`)
- Groups API (`/api/groups`)
- Workspace assignments API (`/api/workspaces/{id}/groups`)
- Org group assignment API (`/api/org/groups/{group_id}/role`)
- User scopes endpoint (`/api/users/me/scopes`)

---

### PR 5: Endpoint Migration
**Branch:** `feat/rbac-endpoint-migration`

- Add `@require_scope` to existing endpoints
- Remove/deprecate `@require_org_admin` style decorators
- Add/verify org/workspace filters in DB queries for IDOR prevention
- Can be split further by domain (workflows, cases, etc.)

---

### PR 6: Frontend - Core
**Branch:** `feat/rbac-frontend-core`

- `ScopeProvider` context
- `ScopeGuard` component
- `useScopes` hook
- API client for `/users/me/scopes`

---

### PR 7: Frontend - Admin UI
**Branch:** `feat/rbac-frontend-admin`

- Org Settings > Roles page
- Org Settings > Groups page
- Workspace Settings > Access page
- Scope picker component

---

## Migration Strategy

### Backwards Compatibility

1. Keep `User.role` column but ignore in auth
2. Existing workspace memberships continue to work
3. System roles (Viewer/Editor/Admin) seeded automatically with appropriate scopes

---

## Execution Enforcement Model

### Decision

Scope checks at the service layer immediately enforce at an action level the allowed scopes.

---

## Resolved Decisions

| # | Question | Decision |
|---|----------|----------|
| 1 | Wildcard matching | `fnmatchcase` with restricted `*` only |
| 2 | Org role scope sets | OWNER-only: `org:delete`, `org:billing:*`, `org:owner:*`. ADMIN gets everything else. MEMBER gets `org:read`, `org:member:read` |
| 3 | Execution enforcement | Snapshot at trigger (see above) |
| 4 | API tokens | Punt to follow-up. Existing tokens inherit user scopes. |
| 5 | Audit logging | Log scope denials (403s) + RBAC CRUD changes. Skip successful checks. |
| 6 | Default action scopes | VIEWER: none, EDITOR: `action:core.*:execute`, ADMIN: `action:*:execute` |

---

## Open Questions

7. **Scope uniqueness boundary**: Are scopes unique per org only, or should some scopes be workspace-scoped (requires schema support)?
8. **Registry uninstall behavior**: When an action is uninstalled/removed, do we mark its scopes `inactive/deprecated` (recommended) vs delete? How does UI handle orphaned scopes on roles?
9. **Scope promotion rules**: If a scope exists as `custom` (pregrant) and later appears via registry sync, do we "promote" the same scope row to `source='registry'` (and update description/source_ref), or create a new scope?

---

## Out of Scope / Dependencies

### Registry Namespace Enforcement (BLOCKING CONCERN)

**Issue:** No namespace enforcement exists today. Users can register custom actions in `core.*`, `ai.*`, or `tools.*` namespaces, potentially shadowing official Tracecat actions.

**Current state:**
- `DEFAULT_NAMESPACE = "core"` - if users don't specify, their UDFs default to `core`
- No validation rejects reserved namespaces for custom registries
- Org-level actions override platform actions with the same key (intentional for customization, but opens squatting risk)

**Risk for RBAC:**
- Scope `action:tools.okta.list_users:execute` could refer to either the official action OR a user-created shadow
- No way to distinguish "grant access to official Okta integration" vs "grant access to user's custom action with same name"

**Proposed fix (separate from RBAC):**
1. Reserve `core.*`, `ai.*`, `tools.*` for `origin = "tracecat_registry"` only
2. Require custom registries to use `custom.*`, `{org_slug}.*`, or other non-reserved prefixes
3. Validate at registry sync time, reject reserved namespace usage

**RBAC assumption:** For now, RBAC treats action keys at face value. The scope `action:{key}:execute` refers to whatever action the user has access to with that key (org-level override or platform). Namespace enforcement is a registry concern to be addressed separately.

**Action:** Discuss with registry owner before RBAC implementation proceeds.
