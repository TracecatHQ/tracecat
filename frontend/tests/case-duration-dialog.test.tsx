/**
 * @jest-environment jsdom
 */

import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import {
  CaseDurationDialog,
  type CaseDurationFormValues,
} from "@/components/cases/case-duration-dialog"

jest.mock("@/lib/hooks", () => ({
  useCaseDropdownDefinitions: () => ({ dropdownDefinitions: [] }),
  useCaseFields: () => ({
    caseFields: [
      { id: "first_reference", display_name: "Shared name", reserved: false },
      { id: "second_reference", display_name: "Shared name", reserved: false },
    ],
  }),
  useCaseTagCatalog: () => ({ caseTags: [] }),
}))

jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: () => "workspace-1",
}))

const initialValues: CaseDurationFormValues = {
  name: "Time to resolution",
  description: "",
  start: { selection: "first", eventType: "fields_changed", filterValues: [] },
  end: { selection: "first", eventType: "case_closed", filterValues: [] },
}

it("distinguishes fields that share a display name", async () => {
  const user = userEvent.setup()
  render(
    <CaseDurationDialog
      open
      onOpenChange={jest.fn()}
      onSubmit={jest.fn()}
      title="Add duration"
      description="Track elapsed time"
      submitLabel="Add duration"
      initialValues={initialValues}
    />
  )

  await user.click(screen.getByText("Select field"))

  expect(screen.getAllByText("Shared name")).toHaveLength(2)
  expect(screen.getByText("first_reference")).toBeInTheDocument()
  expect(screen.getByText("second_reference")).toBeInTheDocument()
})
