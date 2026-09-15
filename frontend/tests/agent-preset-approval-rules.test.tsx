import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { useForm } from "react-hook-form"
import { AgentPresetApprovalRules } from "@/components/agents/agent-preset-approval-rules"
import { Form } from "@/components/ui/form"
import { useEntitlements } from "@/hooks/use-entitlements"

jest.mock("@/hooks/use-entitlements", () => ({
  useEntitlements: jest.fn(),
}))

interface TestValues {
  toolApprovals: Array<{ tool: string; allow: boolean }>
}

const existingRule = { tool: "core.http_request", allow: true }

function TestForm({
  rules = [],
  isSaving = false,
  showRules = true,
  onSubmit = jest.fn(),
}: {
  rules?: TestValues["toolApprovals"]
  isSaving?: boolean
  showRules?: boolean
  onSubmit?: (values: TestValues) => void
}) {
  const form = useForm<TestValues>({
    defaultValues: { toolApprovals: rules },
  })
  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)}>
        {showRules ? (
          <AgentPresetApprovalRules
            isSaving={isSaving}
            actionSuggestions={[
              {
                id: "core.http_request",
                label: "HTTP request",
                value: "core.http_request",
              },
            ]}
          />
        ) : null}
        <button type="submit">Save</button>
      </form>
    </Form>
  )
}

beforeEach(() => {
  jest.mocked(useEntitlements).mockReturnValue({
    hasEntitlement: () => false,
    isLoading: false,
    hasEntitlementData: true,
  })
})

it("prevents OSS users from adding or changing approval rules", async () => {
  const user = userEvent.setup()
  render(<TestForm rules={[existingRule]} />)

  expect(screen.getByRole("button", { name: "Add rule" })).toBeDisabled()
  expect(screen.getByRole("combobox")).toBeDisabled()
  expect(screen.getByRole("switch")).toBeDisabled()
  expect(screen.getByRole("alert")).toHaveTextContent(
    "Approval rules require an Enterprise plan"
  )

  await user.click(screen.getByRole("button", { name: "Add rule" }))
  expect(screen.getAllByRole("switch")).toHaveLength(1)
})

it("lets a downgraded organization remove existing rules and save", async () => {
  const user = userEvent.setup()
  const onSubmit = jest.fn()
  render(<TestForm rules={[existingRule]} onSubmit={onSubmit} />)

  await user.click(screen.getByRole("button", { name: "Remove approval rule" }))
  await user.click(screen.getByRole("button", { name: "Save" }))

  await waitFor(() => expect(onSubmit).toHaveBeenCalled())
  expect(onSubmit.mock.calls[0][0]).toEqual({ toolApprovals: [] })
})

it("keeps rule authoring available with agent add-ons", async () => {
  jest.mocked(useEntitlements).mockReturnValue({
    hasEntitlement: (key) => key === "agent_addons",
    isLoading: false,
    hasEntitlementData: true,
  })
  const user = userEvent.setup()
  const onSubmit = jest.fn()
  render(<TestForm rules={[existingRule]} onSubmit={onSubmit} />)

  expect(screen.getByRole("combobox")).toBeEnabled()
  await user.click(screen.getByRole("switch"))
  await user.click(screen.getByRole("button", { name: "Save" }))
  await waitFor(() => expect(onSubmit).toHaveBeenCalled())
  expect(onSubmit.mock.calls[0][0]).toEqual({
    toolApprovals: [{ ...existingRule, allow: false }],
  })

  await user.click(screen.getByRole("button", { name: "Add rule" }))
  expect(screen.getAllByRole("switch")).toHaveLength(2)
})

it("blocks authoring while entitlements are loading", () => {
  jest.mocked(useEntitlements).mockReturnValue({
    hasEntitlement: () => false,
    isLoading: true,
    hasEntitlementData: false,
  })
  render(<TestForm />)

  expect(screen.getByRole("button", { name: "Add rule" })).toBeDisabled()
  expect(screen.queryByRole("alert")).not.toBeInTheDocument()
})

it("preserves rule edits when the configuration panel unmounts", async () => {
  jest.mocked(useEntitlements).mockReturnValue({
    hasEntitlement: (key) => key === "agent_addons",
    isLoading: false,
    hasEntitlementData: true,
  })
  const user = userEvent.setup()
  const onSubmit = jest.fn()
  const { rerender } = render(
    <TestForm rules={[existingRule]} onSubmit={onSubmit} />
  )

  await user.click(screen.getByRole("switch"))
  rerender(
    <TestForm rules={[existingRule]} onSubmit={onSubmit} showRules={false} />
  )
  await user.click(screen.getByRole("button", { name: "Save" }))
  await waitFor(() => expect(onSubmit).toHaveBeenCalled())
  expect(onSubmit.mock.calls[0][0]).toEqual({
    toolApprovals: [{ ...existingRule, allow: false }],
  })

  rerender(<TestForm rules={[existingRule]} onSubmit={onSubmit} />)
  expect(screen.getByRole("switch")).not.toBeChecked()
})

it("does not allow removing rules while a save is pending", () => {
  render(<TestForm rules={[existingRule]} isSaving />)

  expect(
    screen.getByRole("button", { name: "Remove approval rule" })
  ).toBeDisabled()
})
