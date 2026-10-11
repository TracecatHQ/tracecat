import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { ControlsHeader } from "@/components/nav/controls-header"

jest.mock("next/navigation", () => ({
  usePathname: () => "/workspaces/workspace-synthetic/tables",
  useSearchParams: () => new URLSearchParams(),
  useRouter: () => ({ push: jest.fn() }),
}))
jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: () => "workspace-synthetic",
}))
jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: jest.fn(),
}))
jest.mock("@/components/ui/sidebar", () => ({
  SidebarTrigger: () => null,
}))
jest.mock("@/components/tiptap-templates/simple/simple-editor", () => ({
  SimpleEditor: () => null,
}))
jest.mock("@/components/workspace-sync/resource-sync-actions", () => ({
  WorkspaceResourceSyncActions: () => (
    <button type="button">Sync tables</button>
  ),
}))
jest.mock("@/components/tables/table-create-dialog", () => ({
  CreateTableDialog: ({ open }: { open: boolean }) =>
    open ? <div>Create dialog</div> : null,
}))
jest.mock("@/components/tables/table-import-table-dialog", () => ({
  TableImportTableDialog: ({ open }: { open: boolean }) =>
    open ? <div>Import dialog</div> : null,
}))

describe("Table creation header permissions", () => {
  const mockUseScopeCheck = jest.mocked(useScopeCheck)

  it.each([false, undefined])(
    "does not offer creation while table:create is %s",
    (permission) => {
      mockUseScopeCheck.mockReturnValue(permission)
      render(<ControlsHeader />)

      expect(mockUseScopeCheck).toHaveBeenCalledWith("table:create")
      expect(
        screen.queryByRole("button", { name: "New table" })
      ).not.toBeInTheDocument()
      expect(screen.getByRole("button", { name: "Sync tables" })).toBeEnabled()
    }
  )

  it.each([
    ["Create table", "Create dialog"],
    ["Import from CSV", "Import dialog"],
  ])("allows an authorized user to open %s", async (action, dialog) => {
    mockUseScopeCheck.mockReturnValue(true)
    render(<ControlsHeader />)

    await userEvent.click(screen.getByRole("button", { name: "New table" }))
    await userEvent.click(
      screen.getByRole("menuitem", { name: new RegExp(action) })
    )

    expect(screen.getByText(dialog)).toBeInTheDocument()
  })

  it("removes creation controls and an open dialog if permission is revoked", async () => {
    mockUseScopeCheck.mockReturnValue(true)
    const view = render(<ControlsHeader />)
    await userEvent.click(screen.getByRole("button", { name: "New table" }))
    await userEvent.click(
      screen.getByRole("menuitem", { name: /Create table/ })
    )
    expect(screen.getByText("Create dialog")).toBeInTheDocument()

    mockUseScopeCheck.mockReturnValue(false)
    view.rerender(<ControlsHeader />)

    expect(screen.queryByText("Create dialog")).not.toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "New table" })
    ).not.toBeInTheDocument()
  })
})
