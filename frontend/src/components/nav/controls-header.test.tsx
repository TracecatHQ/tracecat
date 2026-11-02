import { render, screen } from "@testing-library/react"
import { ControlsHeader } from "@/components/nav/controls-header"

let scopes = new Set<string>()

jest.mock("@/components/ai-elements/code-block", () => ({
  CodeBlock: ({ code }: { code: string }) => <pre>{code}</pre>,
  CodeBlockCopyButton: () => null,
}))

jest.mock("next/navigation", () => ({
  usePathname: () => "/workspaces/workspace-1/forms",
  useRouter: () => ({ push: jest.fn() }),
  useSearchParams: () => new URLSearchParams(),
}))
jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: (scope: string) => scopes.has(scope),
}))
jest.mock("@/hooks/use-forms-access", () => ({
  useFormsAccess: () => "enabled",
}))
jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: () => "workspace-1",
}))
jest.mock("@/components/ui/sidebar", () => ({
  SidebarTrigger: () => null,
}))
jest.mock("@/components/workspace-sync/resource-sync-actions", () => ({
  WorkspaceResourceSyncActions: () => null,
}))
jest.mock("@/components/forms/forms-dashboard", () => ({
  CreateFormDialog: () => null,
}))
// Other pages' header actions pull in the ESM-only editor.
jest.mock("@/components/tiptap-templates/simple/simple-editor", () => ({
  SimpleEditor: () => null,
}))

describe("ControlsHeader on the forms list", () => {
  it.each([
    [["form:create", "workflow:read"], true],
    // The Create form dialog picks from the workflow list.
    [["form:create"], false],
    [["workflow:read"], false],
  ])("with %j offers Create form: %p", (granted, shown) => {
    scopes = new Set(granted)

    render(<ControlsHeader />)

    expect(screen.getByRole("heading", { name: "Forms" })).toBeInTheDocument()
    expect(Boolean(screen.queryByRole("button", { name: "Create form" }))).toBe(
      shown
    )
  })
})
