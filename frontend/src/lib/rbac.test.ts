import type { ScopeRead } from "@/client"
import {
  abbreviateName,
  abbreviateRoleName,
  getCategoryPermissionLevel,
  getCategoryScopes,
  getScopesForLevel,
  LEVEL_ACTIONS,
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
  // Resource and action as seeded in tracecat/authz/seeding.py.
  function platformScope(resource: string, action: string): ScopeRead {
    return {
      ...integrationReadScope,
      id: `${resource}:${action}`,
      name: `${resource}:${action}`,
      resource,
      action,
    }
  }
  const scopes = [
    platformScope("form", "read"),
    platformScope("form", "create"),
    platformScope("form", "update"),
    platformScope("form", "delete"),
    platformScope("form", "submit"),
    platformScope("form:submission", "read"),
    platformScope("form:submission", "delete"),
    platformScope("workflow", "read"),
    platformScope("workflow", "execute"),
  ]
  const forms = RESOURCE_CATEGORIES.forms

  it("covers form and submission scopes and nothing else", () => {
    expect(forms).toMatchObject({ label: "Forms", resources: ["form"] })
    expect(
      getCategoryScopes(forms.resources, scopes).map((scope) => scope.id)
    ).toEqual([
      "form:read",
      "form:create",
      "form:update",
      "form:delete",
      "form:submit",
      "form:submission:read",
      "form:submission:delete",
    ])
  })

  it.each([
    ["read", ["form:read", "form:submission:read"]],
    [
      "write",
      ["form:read", "form:create", "form:update", "form:submission:read"],
    ],
    ["execute", ["form:read", "form:submit", "form:submission:read"]],
    [
      "admin",
      [
        "form:read",
        "form:create",
        "form:update",
        "form:delete",
        "form:submit",
        "form:submission:read",
        "form:submission:delete",
      ],
    ],
  ] as const)("maps the %s level to its scopes", (level, expected) => {
    const selected = getScopesForLevel(forms.resources, scopes, level)

    expect(selected).toEqual(expected)
    // The level reads back from the scopes it selects.
    expect(
      getCategoryPermissionLevel(forms.resources, scopes, new Set(selected))
    ).toBe(level)
  })

  it("grants form:submit at Execute and Admin only", () => {
    expect(LEVEL_ACTIONS.execute).toContain("submit")
    expect(LEVEL_ACTIONS.admin).toContain("submit")
    expect(LEVEL_ACTIONS.read).not.toContain("submit")
    expect(LEVEL_ACTIONS.write).not.toContain("submit")
  })

  it("leaves the workflows Execute level as it was", () => {
    expect(
      getScopesForLevel(
        RESOURCE_CATEGORIES.workflows.resources,
        scopes,
        "execute"
      )
    ).toEqual(["workflow:read", "workflow:execute"])
  })
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
