import { render, screen } from "@testing-library/react"
import type { WorkflowRead } from "@/client"
import { TriggerPanel } from "@/components/builder/panel/trigger-panel"
import type { FormsAccess } from "@/hooks/use-forms-access"

let access: FormsAccess = "enabled"
let canReadForms: boolean | undefined = true

jest.mock("@/hooks/use-forms-access", () => ({
  useFormsAccess: () => access,
}))
jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: (scope: string) =>
    scope === "form:read" ? canReadForms : true,
}))
jest.mock("@/hooks/use-entitlements", () => ({
  useEntitlements: () => ({ hasEntitlement: () => false }),
}))
// The Form tab is the selected one, so no other tab's content mounts.
jest.mock("@/providers/builder", () => ({
  useWorkflowBuilder: () => ({
    triggerPanelTab: "trigger-form",
    setTriggerPanelTab: jest.fn(),
  }),
}))
jest.mock("@/providers/workflow", () => ({
  useWorkflow: () => ({ isReadOnly: false }),
}))
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

it.each<[FormsAccess, boolean | undefined, boolean]>([
  ["enabled", true, true],
  ["locked", true, false],
  ["hidden", true, false],
  ["enabled", false, false],
])(
  "shows the Form tab when Forms is %s and form:read is %p: %p",
  (formsAccess, canRead, shown) => {
    access = formsAccess
    canReadForms = canRead

    render(<TriggerPanel workflow={{ id: "wf_1" } as WorkflowRead} />)

    expect(Boolean(screen.queryByRole("tab", { name: "Form" }))).toBe(shown)
    expect(Boolean(screen.queryByText("Form trigger controls"))).toBe(shown)
    expect(screen.getByRole("tab", { name: "Webhook" })).toBeInTheDocument()
  }
)
