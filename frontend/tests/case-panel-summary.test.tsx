import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import type { CaseRead } from "@/client"
import { CasePanelSummary } from "@/components/cases/case-panel-summary"

const CASE_FIXTURE = {
  summary: "Suspicious login activity",
} as CaseRead

describe("CasePanelSummary", () => {
  it("allows shift-enter in the compact summary textarea", () => {
    const updateCase = jest.fn().mockResolvedValue(undefined)

    render(
      <CasePanelSummary
        caseData={CASE_FIXTURE}
        updateCase={updateCase}
        compact
      />
    )

    fireEvent.keyDown(screen.getByRole("textbox"), {
      key: "Enter",
      shiftKey: true,
    })

    expect(updateCase).not.toHaveBeenCalled()
  })

  it("submits compact summary edits on enter", async () => {
    const updateCase = jest.fn().mockResolvedValue(undefined)

    render(
      <CasePanelSummary
        caseData={CASE_FIXTURE}
        updateCase={updateCase}
        compact
      />
    )

    fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter" })

    await waitFor(() => {
      expect(updateCase).toHaveBeenCalledWith({
        summary: "Suspicious login activity",
      })
    })
  })
})
