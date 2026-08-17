/**
 * @jest-environment jsdom
 */

import { render, screen } from "@testing-library/react"
import type { CaseDurationDefinitionRead } from "@/client"
import { CaseDurationsTable } from "@/components/cases/case-durations-table"

jest.mock("@/components/cases/update-case-duration-dialog", () => ({
  UpdateCaseDurationDialog: () => null,
}))

jest.mock("@/lib/hooks", () => ({
  useCaseFields: () => ({
    caseFields: [
      {
        id: "analyst_verdict_v2",
        display_name: "Final determination",
      },
    ],
  }),
  useCaseTagCatalog: () => ({ caseTags: [] }),
}))

jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: () => "workspace-1",
}))

const duration: CaseDurationDefinitionRead = {
  id: "duration-1",
  name: "Time to determination",
  description: null,
  start_anchor: {
    event_type: "fields_changed",
    filters: { field_ids: ["analyst_verdict_v2"] },
    selection: "first",
  },
  end_anchor: {
    event_type: "case_closed",
    selection: "first",
  },
}

describe("CaseDurationsTable", () => {
  it("shows field display names in duration summaries", () => {
    render(
      <CaseDurationsTable
        durations={[duration]}
        onDeleteDuration={jest.fn()}
        onUpdateDuration={jest.fn()}
      />
    )

    expect(screen.getByText("Final determination")).toBeInTheDocument()
    expect(screen.queryByText("analyst_verdict_v2")).not.toBeInTheDocument()
  })
})
