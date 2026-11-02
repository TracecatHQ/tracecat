import { fireEvent, render, screen } from "@testing-library/react"
import { useState } from "react"
import type { WorkspacePackageRead } from "@/client"
import {
  PackageResourcesDialog,
  PackageSummary,
} from "@/components/workspace-sync/package-picker"

const mockUser = { isSuperuser: true }

jest.mock("@/hooks/use-auth", () => ({
  useAuth: () => ({ user: mockUser }),
}))

jest.mock("next/link", () => ({
  __esModule: true,
  default: ({
    children,
    href,
  }: {
    children: React.ReactNode
    href: string
  }) => <a href={href}>{children}</a>,
}))

const PACKAGE: WorkspacePackageRead = {
  is_source: true,
  ref: "main",
  patterns: ["workflows/triage/**"],
  resources: [
    {
      resource_type: "workflow",
      source_id: "triage",
      local_id: "wf-1",
      name: "triage",
      path: "workflows/triage/workflow.yml",
      pattern: "workflows/triage/**",
      status: "explicit",
    },
    {
      resource_type: "variable",
      source_id: "default/settings",
      local_id: "var-1",
      name: "settings",
      path: "variables/default/settings.yml",
      pattern: "variables/default/settings.yml",
      status: "implied",
      implied_by: ["triage"],
    },
  ],
}

const SAME_GROUP_PACKAGE: WorkspacePackageRead = {
  is_source: true,
  ref: "main",
  patterns: ["workflows/parent/**"],
  resources: [
    {
      resource_type: "workflow",
      source_id: "parent",
      local_id: "wf-parent",
      name: "parent",
      path: "workflows/parent/workflow.yml",
      pattern: "workflows/parent/**",
      status: "explicit",
    },
    {
      resource_type: "workflow",
      source_id: "child",
      local_id: "wf-child",
      name: "child",
      path: "workflows/child/workflow.yml",
      pattern: "workflows/child/**",
      status: "implied",
      implied_by: ["parent"],
    },
  ],
}

const USES_SETTINGS = new Set(["workflow:triage"])

function renderDialog(
  selected: Set<string>,
  workspacePackage: WorkspacePackageRead = PACKAGE,
  onApply = jest.fn()
) {
  render(
    <PackageResourcesDialog
      open
      onOpenChange={jest.fn()}
      workspacePackage={workspacePackage}
      selected={selected}
      onApply={onApply}
    />
  )
  return onApply
}

function expandGroups() {
  for (const group of screen.getAllByRole("button", { expanded: false })) {
    fireEvent.click(group)
  }
}

describe("PackageResourcesDialog", () => {
  it("starts with every group collapsed", () => {
    renderDialog(USES_SETTINGS)

    expect(screen.queryByRole("checkbox", { name: /^triage/ })).toBeNull()
    expect(screen.queryByRole("checkbox", { name: /^settings/ })).toBeNull()
    fireEvent.click(screen.getByRole("button", { name: /Workflows/ }))
    expect(screen.getByRole("checkbox", { name: /^triage/ })).toBeVisible()
    expect(screen.getByText("workflows/triage/workflow.yml")).toBeVisible()
  })

  it("keeps an implied resource while the resource using it is checked", () => {
    renderDialog(USES_SETTINGS)
    expandGroups()

    const dependency = screen.getByRole("checkbox", { name: /settings/ })
    expect(dependency).toBeChecked()
    expect(dependency).toBeDisabled()
    expect(screen.getByText(/Included because triage uses it/)).toBeVisible()
    expect(screen.getByText(/2 of 2 in the package/)).toBeInTheDocument()
  })

  it("drops an implied resource once its last user is unchecked", () => {
    renderDialog(USES_SETTINGS)
    expandGroups()

    fireEvent.click(screen.getByRole("checkbox", { name: /^triage/ }))

    const dependency = screen.getByRole("checkbox", { name: /settings/ })
    expect(dependency).not.toBeChecked()
    expect(dependency).toBeEnabled()
    expect(screen.getByText(/0 of 2 in the package/)).toBeInTheDocument()
  })

  it("applies a draft only when asked", () => {
    const onApply = renderDialog(USES_SETTINGS)
    expandGroups()

    fireEvent.click(screen.getByRole("checkbox", { name: /^triage/ }))
    expect(onApply).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole("button", { name: "Apply selection" }))
    expect(onApply).toHaveBeenCalledWith(new Set())
  })

  it("discards a draft when closed without applying", () => {
    const onApply = jest.fn()
    function ClosableDialog() {
      const [open, setOpen] = useState(true)
      return (
        <>
          <button type="button" onClick={() => setOpen(true)}>
            Reopen
          </button>
          <PackageResourcesDialog
            open={open}
            onOpenChange={setOpen}
            workspacePackage={PACKAGE}
            selected={USES_SETTINGS}
            onApply={onApply}
          />
        </>
      )
    }
    render(<ClosableDialog />)
    expandGroups()
    fireEvent.click(screen.getByRole("checkbox", { name: /^triage/ }))
    expect(screen.getByText(/0 of 2 in the package/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: "Close" }))
    fireEvent.click(screen.getByRole("button", { name: "Reopen" }))

    expect(screen.getByText(/2 of 2 in the package/)).toBeInTheDocument()
    expect(onApply).not.toHaveBeenCalled()
  })

  it("restarts the draft when the package changes while open", () => {
    const onApply = jest.fn()
    function dialog(workspacePackage: WorkspacePackageRead) {
      return (
        <PackageResourcesDialog
          open
          onOpenChange={jest.fn()}
          workspacePackage={workspacePackage}
          selected={USES_SETTINGS}
          onApply={onApply}
        />
      )
    }
    const { rerender } = render(dialog(PACKAGE))
    expandGroups()
    fireEvent.click(screen.getByRole("checkbox", { name: /^triage/ }))
    expect(screen.getByText(/0 of 2 in the package/)).toBeInTheDocument()

    rerender(dialog({ ...PACKAGE }))

    expect(screen.getByText(/2 of 2 in the package/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Apply selection" }))
    expect(onApply).toHaveBeenCalledWith(USES_SETTINGS)
  })

  it("checks a whole group at once, leaving locked resources alone", () => {
    const onApply = renderDialog(new Set())

    fireEvent.click(
      screen.getByRole("checkbox", { name: /Select all Workflows/i })
    )
    fireEvent.click(screen.getByRole("button", { name: "Apply selection" }))

    expect(onApply).toHaveBeenCalledWith(new Set(["workflow:triage"]))
  })

  it("filters resources by name across groups", () => {
    renderDialog(USES_SETTINGS)

    fireEvent.change(
      screen.getByRole("textbox", { name: "Search resources" }),
      {
        target: { value: "sett" },
      }
    )

    expect(screen.getByRole("checkbox", { name: /settings/ })).toBeVisible()
    expect(screen.queryByRole("checkbox", { name: /^triage/ })).toBeNull()
    expect(screen.getByRole("button", { name: /Variables/ })).toBeDisabled()
  })

  it("keeps a dependency implied when its group is toggled off and on", () => {
    const onApply = jest.fn()
    render(
      <PackageResourcesDialog
        open
        onOpenChange={jest.fn()}
        workspacePackage={SAME_GROUP_PACKAGE}
        selected={new Set(["workflow:parent"])}
        onApply={onApply}
      />
    )
    const group = screen.getByRole("checkbox", {
      name: /Select all Workflows/i,
    })

    fireEvent.click(group)
    expect(screen.getByText(/0 of 2 in the package/)).toBeInTheDocument()
    fireEvent.click(group)
    expect(screen.getByText(/2 of 2 in the package/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: "Apply selection" }))
    expect(onApply).toHaveBeenCalledWith(new Set(["workflow:parent"]))
  })

  it("marks a partly selected group", () => {
    renderDialog(new Set(), {
      ...SAME_GROUP_PACKAGE,
      resources: SAME_GROUP_PACKAGE.resources?.map((resource) => ({
        ...resource,
        status: "explicit",
      })),
    })
    expandGroups()

    fireEvent.click(screen.getByRole("checkbox", { name: /^child/ }))

    expect(
      screen.getByRole("checkbox", { name: /Select all Workflows/i })
    ).toHaveAttribute("aria-checked", "mixed")
  })
})

describe("PackageSummary", () => {
  beforeEach(() => {
    mockUser.isSuperuser = true
  })

  it("names the admin page without linking it for other users", () => {
    mockUser.isSuperuser = false
    render(
      <PackageSummary
        workspacePackage={PACKAGE}
        selected={USES_SETTINGS}
        isChanged={false}
        onApply={jest.fn()}
      />
    )

    expect(screen.getByText(/Admin, Managed Workspaces/)).toBeInTheDocument()
    expect(screen.queryByRole("link")).toBeNull()
  })

  it("counts the package and opens the dialog", () => {
    render(
      <PackageSummary
        workspacePackage={PACKAGE}
        selected={USES_SETTINGS}
        isChanged={false}
        onApply={jest.fn()}
      />
    )

    expect(screen.getByText(/2 of 2 resources/)).toBeInTheDocument()
    expect(
      screen.getByText(/Publishing changes no customer workspace/)
    ).toBeInTheDocument()
    expect(
      screen.getByRole("link", { name: "Admin, Managed Workspaces" })
    ).toHaveAttribute("href", "/admin/managed-package")

    fireEvent.click(screen.getByRole("button", { name: "Edit resources" }))
    expect(
      screen.getByRole("dialog", { name: "Choose package resources" })
    ).toBeInTheDocument()
  })
})
