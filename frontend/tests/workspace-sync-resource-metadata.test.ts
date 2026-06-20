import { getWorkspaceSyncResourceLabel } from "@/components/workspace-sync/resource-metadata"

describe("getWorkspaceSyncResourceLabel", () => {
  it("returns labels for known resource types", () => {
    expect(getWorkspaceSyncResourceLabel("workflow")).toBe("Workflows")
  })

  it("preserves unknown resource types that match object prototype keys", () => {
    expect(getWorkspaceSyncResourceLabel("toString")).toBe("toString")
  })
})
