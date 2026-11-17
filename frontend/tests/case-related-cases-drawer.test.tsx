/**
 * @jest-environment jsdom
 */

import { act, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { ReactNode } from "react"
import type { CaseReadMinimal } from "@/client"
import { CaseRelatedCasesDrawer } from "@/components/cases/case-related-cases-drawer"
import { useLinkedCases } from "@/hooks/use-linked-cases"

jest.mock("@/hooks/use-linked-cases", () => ({
  useLinkedCases: jest.fn(),
}))

jest.mock("@/hooks/use-entitlements", () => ({
  useEntitlements: () => ({ hasEntitlement: () => false }),
}))

jest.mock("@/hooks/use-workspace", () => ({
  useWorkspaceMembers: () => ({ members: [] }),
}))

jest.mock("@/lib/hooks", () => ({
  useCaseDropdownDefinitions: () => ({ dropdownDefinitions: [] }),
  useCaseTagCatalog: () => ({ caseTags: [] }),
  useGetCase: () => ({
    caseData: { fields: [] },
    caseDataIsLoading: false,
    caseDataError: null,
  }),
}))

jest.mock("@/components/json-viewer", () => ({
  JsonViewWithControls: ({ src }: { src: unknown }) => (
    <pre data-testid="row-json">{JSON.stringify(src)}</pre>
  ),
}))

jest.mock("@/components/cases/case-value-drawer", () => ({
  CaseValueDrawer: ({
    open,
    title,
    description,
    children,
  }: {
    open: boolean
    title: string
    description?: string
    children: ReactNode
  }) =>
    open ? (
      <div>
        <h2>{title}</h2>
        <p>{description}</p>
        {children}
      </div>
    ) : null,
}))

jest.mock("@/components/cases/case-item", () => ({
  CaseItem: ({
    caseData,
    onClick,
  }: {
    caseData: CaseReadMinimal
    onClick: () => void
  }) => (
    <button type="button" onClick={onClick}>
      {caseData.summary}
    </button>
  ),
}))

const relatedCase = {
  id: "case-2",
  short_id: "CASE-0002",
  summary: "Unexpected administrator login",
  status: "in_progress",
  priority: "high",
  severity: "critical",
  assignee: null,
  tags: [],
  dropdown_values: [],
  num_tasks_completed: 0,
  num_tasks_total: 0,
  created_at: "2026-10-07T00:00:00Z",
  updated_at: "2026-10-07T00:00:00Z",
} as unknown as CaseReadMinimal

function renderDrawer() {
  return render(
    <CaseRelatedCasesDrawer
      target={{
        tableId: "table-1",
        tableName: "investigation_assets",
        rowId: "row-1",
        rowData: { hostname: "gateway-01", owner: null },
      }}
      onClose={jest.fn()}
      caseId="case-1"
      workspaceId="ws-1"
    />
  )
}

describe("CaseRelatedCasesDrawer", () => {
  beforeEach(() => {
    jest.mocked(useLinkedCases).mockReturnValue({
      linkedCases: [relatedCase],
      linkedCasesIsLoading: false,
      linkedCasesError: null,
      hasNextPage: false,
      isFetchingNextPage: false,
      fetchNextPage: jest.fn(),
    } as unknown as ReturnType<typeof useLinkedCases>)
    jest.spyOn(window, "open").mockImplementation(() => null)
  })

  afterEach(() => {
    jest.restoreAllMocks()
  })

  it("shows the row's contents above its related cases", () => {
    renderDrawer()

    expect(
      screen.getByText("Cases related to selected linked row")
    ).toBeInTheDocument()
    expect(screen.getByText("Linked row in investigation_assets")).toBeVisible()
    expect(screen.getByTestId("row-json")).toHaveTextContent(
      JSON.stringify({ hostname: "gateway-01", owner: null })
    )
  })

  it("pins a case's card on click, with a link that opens it in a new tab", async () => {
    const user = userEvent.setup()
    renderDrawer()

    await user.click(screen.getByText("Unexpected administrator login"))

    const link = await screen.findByRole("link", { name: /Open case/ })
    expect(link).toHaveAttribute("href", "/workspaces/ws-1/cases/case-2")
    expect(link).toHaveAttribute("target", "_blank")
    expect(window.open).not.toHaveBeenCalled()

    await user.keyboard("{Escape}")
    expect(screen.queryByRole("link", { name: /Open case/ })).toBeNull()
  })

  it("opens the case in a new tab on Ctrl-click instead of pinning", async () => {
    const user = userEvent.setup()
    renderDrawer()

    await user.keyboard("{Control>}")
    await user.click(screen.getByText("Unexpected administrator login"))
    await user.keyboard("{/Control}")

    expect(window.open).toHaveBeenCalledWith(
      "/workspaces/ws-1/cases/case-2",
      "_blank",
      "noopener,noreferrer"
    )
    expect(screen.queryByRole("link", { name: /Open case/ })).toBeNull()
    // A focused row would reopen its card when the tab comes back.
    expect(screen.getByText("Unexpected administrator login")).not.toHaveFocus()
  })

  it("keeps a pinned card when another case is Ctrl-clicked", async () => {
    jest.mocked(useLinkedCases).mockReturnValue({
      linkedCases: [
        relatedCase,
        {
          ...relatedCase,
          id: "case-3",
          short_id: "CASE-0003",
          summary: "Outbound connection to blocked domain",
        },
      ],
      linkedCasesIsLoading: false,
      linkedCasesError: null,
      hasNextPage: false,
      isFetchingNextPage: false,
      fetchNextPage: jest.fn(),
    } as unknown as ReturnType<typeof useLinkedCases>)
    const user = userEvent.setup()
    renderDrawer()

    await user.click(screen.getByText("Unexpected administrator login"))
    await user.keyboard("{Control>}")
    await user.click(screen.getByText("Outbound connection to blocked domain"))
    await user.keyboard("{/Control}")
    await user.unhover(
      screen.getByText("Outbound connection to blocked domain")
    )

    expect(window.open).toHaveBeenCalledWith(
      "/workspaces/ws-1/cases/case-3",
      "_blank",
      "noopener,noreferrer"
    )
    expect(screen.getByRole("link", { name: /Open case/ })).toHaveAttribute(
      "href",
      "/workspaces/ws-1/cases/case-2"
    )
  })

  it("drops a hovered card when the window loses focus", async () => {
    const user = userEvent.setup()
    renderDrawer()

    await user.hover(screen.getByText("Unexpected administrator login"))
    expect(
      await screen.findByRole("link", { name: /Open case/ })
    ).toBeInTheDocument()

    act(() => {
      window.dispatchEvent(new Event("blur"))
    })
    expect(screen.queryByRole("link", { name: /Open case/ })).toBeNull()
  })
})
