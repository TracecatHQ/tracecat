import { suggestCopyName } from "@/components/managed/detach-dialog"

jest.mock("next/navigation", () => ({ useRouter: jest.fn() }))
jest.mock("@/hooks/use-managed-resources", () => ({
  useDetachManagedResource: jest.fn(),
}))
jest.mock("@/providers/workspace-id", () => ({ useWorkspaceId: jest.fn() }))

describe("suggestCopyName", () => {
  it("appends a copy suffix that matches the resource's naming rules", () => {
    expect(suggestCopyName("workflow", "triage")).toBe("triage-copy")
    expect(suggestCopyName("table", "assets")).toBe("assets_copy")
  })

  it("truncates long names to fit the 100 character limit", () => {
    const name = suggestCopyName("workflow", "a".repeat(100))
    expect(name).toHaveLength(100)
    expect(name.endsWith("-copy")).toBe(true)
  })
})
