import { render, screen } from "@testing-library/react"
import type { WorkflowRead } from "@/client"
import { TriggerPanel } from "@/components/builder/panel/trigger-panel"
import type { FormsAccess } from "@/lib/forms/access"

let mockFormsAccess: FormsAccess = "enabled"
let mockCanReadForms: boolean | undefined = true

jest.mock("@/hooks/use-forms-access", () => ({
  useFormsAccess: () => mockFormsAccess,
}))
jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: (scope: string) =>
    scope === "form:read" ? mockCanReadForms : true,
}))
jest.mock("@/hooks/use-entitlements", () => ({
  useEntitlements: () => ({ hasEntitlement: () => false }),
}))
// The Form tab is the selected one, so no other tab's content mounts.
jest.mock("@/providers/builder", () => ({
  useWorkflowBuilder: () => ({
    triggerPanelTab: jest.requireActual(
      "@/components/builder/panel/trigger-panel-tabs"
    ).TriggerPanelTabs.form,
    setTriggerPanelTab: jest.fn(),
  }),
}))
jest.mock("@/providers/workflow", () => ({ useWorkflow: jest.fn() }))
jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: () => "workspace-1",
}))
jest.mock("@/lib/hooks", () => ({}))
jest.mock("@/components/icons", () => ({ getIcon: () => null }))
jest.mock("@/components/builder/canvas/trigger-node", () => ({
  TriggerTypename: "trigger",
}))
jest.mock("@/components/forms/form-trigger-controls", () => ({
  FormTriggerControls: () => <div>Form trigger controls</div>,
}))

const workflow = { id: "workflow-1" } as WorkflowRead

function renderPanel() {
  render(<TriggerPanel workflow={workflow} />)
  return {
    tab: screen.queryByRole("tab", { name: "Form" }),
    controls: screen.queryByText("Form trigger controls"),
  }
}

beforeEach(() => {
  mockFormsAccess = "enabled"
  mockCanReadForms = true
})

describe("TriggerPanel Form tab", () => {
  it("shows with Forms enabled and form:read", () => {
    const { tab, controls } = renderPanel()

    expect(tab).toBeInTheDocument()
    expect(controls).toBeInTheDocument()
    // The existing tabs are untouched.
    expect(screen.getByRole("tab", { name: "Webhook" })).toBeInTheDocument()
    expect(screen.getByRole("tab", { name: "Schedules" })).toBeInTheDocument()
  })

  it.each<FormsAccess>(["hidden", "locked", "loading"])(
    "is absent when Forms is %s",
    (access) => {
      mockFormsAccess = access

      const { tab, controls } = renderPanel()

      expect(tab).not.toBeInTheDocument()
      expect(controls).not.toBeInTheDocument()
      expect(screen.getByRole("tab", { name: "Webhook" })).toBeInTheDocument()
    }
  )

  it.each([false, undefined])("is absent when form:read is %p", (canRead) => {
    mockCanReadForms = canRead

    const { tab, controls } = renderPanel()

    expect(tab).not.toBeInTheDocument()
    expect(controls).not.toBeInTheDocument()
  })
})
