/**
 * @jest-environment jsdom
 */

import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { WorkflowTriggerForm } from "@/components/cases/workflow-trigger-form"
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import type { TracecatJsonSchema } from "@/lib/schema"

beforeAll(() => {
  if (!HTMLElement.prototype.hasPointerCapture) {
    Object.defineProperty(HTMLElement.prototype, "hasPointerCapture", {
      value: () => false,
    })
  }
  if (!HTMLElement.prototype.setPointerCapture) {
    Object.defineProperty(HTMLElement.prototype, "setPointerCapture", {
      value: () => undefined,
    })
  }
  if (!HTMLElement.prototype.releasePointerCapture) {
    Object.defineProperty(HTMLElement.prototype, "releasePointerCapture", {
      value: () => undefined,
    })
  }
})

const schema: TracecatJsonSchema = {
  type: "object",
  properties: {
    destination: { type: "string" },
  },
}

describe("WorkflowTriggerForm", () => {
  it("shows display names for case-field value suggestions", async () => {
    const user = userEvent.setup()
    render(
      <AlertDialog open>
        <AlertDialogContent>
          <AlertDialogTitle>Trigger workflow</AlertDialogTitle>
          <AlertDialogDescription>Provide inputs.</AlertDialogDescription>
          <WorkflowTriggerForm
            schema={schema}
            caseId="case-1"
            caseFields={{ analyst_verdict_v2: "Resolved" }}
            caseFieldDisplayNameById={
              new Map([["analyst_verdict_v2", "Final determination"]])
            }
            groupCaseFields={false}
            onSubmit={jest.fn()}
            isSubmitting={false}
          />
        </AlertDialogContent>
      </AlertDialog>
    )

    await user.click(screen.getByRole("button", { name: "Add case value" }))

    expect(
      screen.getByText("Case field • Final determination")
    ).toBeInTheDocument()
    expect(
      screen.queryByText("Case field • Analyst verdict v2")
    ).not.toBeInTheDocument()

    await user.type(
      screen.getByPlaceholderText("Search case values..."),
      "Final determination"
    )
    expect(
      screen.getByText("Case field • Final determination")
    ).toBeInTheDocument()
  })
})
