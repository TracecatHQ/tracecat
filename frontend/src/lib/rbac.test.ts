import type { ScopeRead } from "@/client"
import {
  getCategoryScopes,
  getScopesForLevel,
  RESOURCE_CATEGORIES,
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
