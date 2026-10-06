import type { ScopeRead } from "@/client"
import {
  abbreviateName,
  abbreviateRoleName,
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
