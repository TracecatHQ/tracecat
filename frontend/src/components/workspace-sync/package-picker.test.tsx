import { fireEvent, render, screen } from "@testing-library/react"
import type { WorkspacePackageRead } from "@/client"
import { PackagePicker } from "@/components/workspace-sync/package-picker"

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

function renderPicker(selected: Set<string>) {
  render(
    <PackagePicker
      workspacePackage={PACKAGE}
      selected={selected}
      isChanged={selected.size === 0}
      onToggle={jest.fn()}
    />
  )
  for (const group of screen.getAllByRole("button", { expanded: false })) {
    fireEvent.click(group)
  }
  return screen.getByRole("checkbox", { name: /settings/ })
}

describe("PackagePicker", () => {
  it("keeps an implied resource while the resource using it is checked", () => {
    const dependency = renderPicker(new Set(["workflow:triage"]))
    expect(dependency).toBeChecked()
    expect(dependency).toBeDisabled()
    expect(screen.getByText("2 of 2")).toBeInTheDocument()
  })

  it("drops an implied resource once its last user is unchecked", () => {
    const dependency = renderPicker(new Set())
    expect(dependency).not.toBeChecked()
    expect(dependency).toBeEnabled()
    expect(screen.getByText("0 of 2")).toBeInTheDocument()
  })
})
