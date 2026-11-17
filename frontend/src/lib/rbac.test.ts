import type { ScopeRead } from "@/client"
import {
  abbreviateName,
  abbreviateRoleName,
  getCategoryPermissionLevel,
  getCategoryScopes,
  getScopesForLevel,
  organizationTier,
  RESOURCE_CATEGORIES,
  rolesForAssignmentEdit,
  rolesForScope,
} from "@/lib/rbac"

const integrationReadScope: ScopeRead = {
  id: "integration-read",
  name: "integration:read",
  resource: "integration",
  action: "read",
  description: "View integrations and provider metadata",
  source: "platform",
  source_ref: null,
  organization_id: null,
  created_at: "",
  updated_at: "",
}

describe("RESOURCE_CATEGORIES", () => {
  it("exposes integration scopes in the service account permission editor", () => {
    const integrations = RESOURCE_CATEGORIES.integrations

    expect(integrations).toMatchObject({
      label: "Integrations",
      resources: ["integration"],
    })
    expect(
      getCategoryScopes(integrations.resources, [integrationReadScope])
    ).toEqual([integrationReadScope])
    expect(
      getScopesForLevel(integrations.resources, [integrationReadScope], "read")
    ).toEqual([integrationReadScope.id])
  })
})

describe("forms category", () => {
  // Resources and actions as seeded in tracecat/authz/seeding.py.
  const scopes = [
    "form:read",
    "form:create",
    "form:update",
    "form:delete",
    "form:submit",
    "form:submission:read",
    "form:submission:delete",
    "workflow:execute",
  ].map((id) => ({
    ...integrationReadScope,
    id,
    name: id,
    resource: id.slice(0, id.lastIndexOf(":")),
    action: id.slice(id.lastIndexOf(":") + 1),
  }))
  const { resources } = RESOURCE_CATEGORIES.forms

  it.each([
    ["read", ["form:read", "form:submission:read"]],
    [
      "write",
      ["form:read", "form:create", "form:update", "form:submission:read"],
    ],
    // Executing a form is submitting it.
    ["execute", ["form:read", "form:submit", "form:submission:read"]],
    ["admin", scopes.slice(0, 7).map((scope) => scope.id)],
  ] as const)(
    "maps the %s level to its form scopes and back",
    (level, expected) => {
      const selected = getScopesForLevel(resources, scopes, level)

      expect(selected).toEqual(expected)
      expect(
        getCategoryPermissionLevel(resources, scopes, new Set(selected))
      ).toBe(level)
    }
  )
})

describe("rolesForScope and rolesForAssignmentEdit", () => {
  const scope = (name: string) => ({
    id: name,
    name,
    resource: name.split(":")[0],
    action: "read",
    source: "platform" as const,
    created_at: "2026-01-01",
    updated_at: "2026-01-01",
  })
  const orgRole = { id: "org", scopes: [scope("org:read")] }
  const workspaceRole = { id: "ws", scopes: [scope("workflow:read")] }
  const roles = [orgRole, workspaceRole]

  it("offers every role org-wide and only workspace roles on a workspace", () => {
    expect(rolesForScope(roles, null)).toEqual(roles)
    expect(rolesForScope(roles, "w1")).toEqual([workspaceRole])
  })

  it("returns a current role that no longer fits as legacy", () => {
    expect(rolesForAssignmentEdit(roles, "w1", "org")).toEqual({
      options: [workspaceRole],
      legacy: orgRole,
    })
    expect(rolesForAssignmentEdit(roles, "w1", "ws").legacy).toBeUndefined()
  })
})

describe("abbreviateRoleName", () => {
  it("uses uppercased word initials", () => {
    expect(abbreviateRoleName("Workspace Admin")).toBe("WA")
    expect(abbreviateRoleName("Organization Owner")).toBe("OO")
    expect(abbreviateRoleName("  security  analyst ")).toBe("SA")
    expect(abbreviateRoleName("Viewer")).toBe("V")
  })
})

describe("abbreviateName", () => {
  it("uses the first three non-space characters, uppercased", () => {
    expect(abbreviateName("Engineering")).toBe("ENG")
    expect(abbreviateName("a b team")).toBe("ABT")
    expect(abbreviateName("IT")).toBe("IT")
  })
})

describe("organizationTier", () => {
  it("maps owner and admin slugs and defaults to member", () => {
    expect(organizationTier("organization-owner")).toBe("Owner")
    expect(organizationTier("organization-admin")).toBe("Admin")
    expect(organizationTier("organization-member")).toBe("Member")
    expect(organizationTier("workspace-admin")).toBe("Member")
    expect(organizationTier(null)).toBe("Member")
    expect(organizationTier(undefined)).toBe("Member")
  })
})

describe("Git sync category", () => {
  // Upgraded databases keep the deprecated sync rows.
  const toScope = (id: string) => ({
    ...integrationReadScope,
    id,
    name: id,
    resource: id.slice(0, id.indexOf(":")),
    action: id.slice(id.indexOf(":") + 1),
  })
  const scopes = [
    "workspace_sync:push",
    "workspace_sync:pull",
    "workspace_sync:sync",
    "workflow:sync",
    "workflow:read",
  ].map(toScope)
  const { resources } = RESOURCE_CATEGORIES.gitSync
  const workflows = RESOURCE_CATEGORIES.workflows.resources

  it("lists both deprecated scopes so setting a level removes them", () => {
    expect(getCategoryScopes(resources, scopes).map((s) => s.id)).toEqual([
      "workspace_sync:push",
      "workspace_sync:pull",
      "workspace_sync:sync",
      "workflow:sync",
    ])
    expect(getCategoryScopes(workflows, scopes).map((s) => s.id)).toEqual([
      "workflow:read",
    ])
  })

  it("ignores deprecated scopes when push or pull is selected", () => {
    const migrated = new Set(scopes.map((s) => s.id))
    expect(getCategoryPermissionLevel(resources, scopes, migrated)).toBe(
      "admin"
    )
    // On their own they still grant access on older app versions.
    expect(
      getCategoryPermissionLevel(resources, scopes, new Set(["workflow:sync"]))
    ).toBe("mixed")
  })

  it.each([
    // Editors push; admins also pull.
    ["write", ["workspace_sync:push"]],
    ["admin", ["workspace_sync:push", "workspace_sync:pull"]],
  ] as const)(
    "maps the %s level to its Git sync scopes and back",
    (level, expected) => {
      const selected = getScopesForLevel(resources, scopes, level)

      expect(selected).toEqual(expected)
      expect(
        getCategoryPermissionLevel(resources, scopes, new Set(selected))
      ).toBe(level)
    }
  )
})
