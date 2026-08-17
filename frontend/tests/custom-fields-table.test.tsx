/**
 * @jest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen } from "@testing-library/react"
import type { CaseFieldReadMinimal } from "@/client"
import { CustomFieldsTable } from "@/components/cases/custom-fields-table"

jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: () => "workspace-1",
}))

const fields: CaseFieldReadMinimal[] = [
  {
    id: "analyst_verdict",
    display_name: "Analyst verdict",
    type: "TEXT",
    description: "",
    nullable: true,
    default: null,
    reserved: false,
    options: null,
    kind: null,
    required_on_closure: false,
  },
]

describe("CustomFieldsTable", () => {
  it("shows the human name and reference as separate columns", () => {
    const queryClient = new QueryClient()
    render(
      <QueryClientProvider client={queryClient}>
        <CustomFieldsTable fields={fields} onDeleteField={jest.fn()} />
      </QueryClientProvider>
    )

    expect(screen.getByText("Field name")).toBeInTheDocument()
    expect(screen.getByText("Reference")).toBeInTheDocument()
    expect(screen.getByText("Analyst verdict")).toBeInTheDocument()
    expect(screen.getByText("analyst_verdict")).toBeInTheDocument()
    expect(
      screen.getByPlaceholderText("Filter fields by name...")
    ).toBeInTheDocument()
  })
})
