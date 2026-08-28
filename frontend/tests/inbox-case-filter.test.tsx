import {
  act,
  fireEvent,
  render,
  renderHook,
  screen,
  waitFor,
} from "@testing-library/react"
import type { ReactNode } from "react"
import { type InboxListItemsResponse, inboxListItems } from "@/client"
import { CaseAgentRunsAction } from "@/components/cases/case-agent-runs-action"
import { InboxHeader } from "@/components/inbox/inbox-header"
import { useInbox } from "@/hooks/use-inbox"
import {
  getCaseAgentRunsHref,
  getInboxHrefWithoutCaseFilter,
  parseInboxCaseId,
} from "@/lib/inbox"
import { QueryClient, QueryClientProvider } from "@/lib/query"

const mockUseScopeCheck = jest.fn<boolean | undefined, [string]>()
let mockAgentAddonsEnabled = true
let mockEntitlementsLoading = false

jest.mock("@/client", () => ({
  approvalsDeleteApproval: jest.fn(),
  inboxListItems: jest.fn(),
}))

jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: (scope: string) => mockUseScopeCheck(scope),
}))

jest.mock("@/hooks/use-entitlements", () => ({
  useEntitlements: () => ({
    hasEntitlement: (key: string) =>
      key === "agent_addons" && mockAgentAddonsEnabled,
    isLoading: mockEntitlementsLoading,
  }),
}))

jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: () => "workspace-test",
}))

const CASE_ID = "11111111-1111-4111-8111-111111111111"
const OTHER_CASE_ID = "22222222-2222-4222-8222-222222222222"
const EMPTY_PAGE: InboxListItemsResponse = {
  items: [],
  has_more: false,
  next_cursor: null,
}

function createWrapper(queryClient: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    )
  }
}

describe("Inbox case filtering", () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockUseScopeCheck.mockReturnValue(true)
    mockAgentAddonsEnabled = true
    mockEntitlementsLoading = false
  })

  it("validates case URL state and preserves unrelated state when clearing", () => {
    expect(parseInboxCaseId(CASE_ID)).toBe(CASE_ID)
    expect(parseInboxCaseId("not-a-case-id")).toBeNull()
    expect(parseInboxCaseId(null)).toBeNull()
    expect(getCaseAgentRunsHref("workspace-test", CASE_ID)).toBe(
      `/workspaces/workspace-test/inbox?caseId=${CASE_ID}`
    )
    expect(
      getInboxHrefWithoutCaseFilter(
        "/workspaces/workspace-test/inbox",
        `caseId=${CASE_ID}&search=failed+run`
      )
    ).toBe("/workspaces/workspace-test/inbox?search=failed+run")
  })

  it("filters every group and starts fresh cursor streams when the case changes", async () => {
    const mockListItems = jest.mocked(inboxListItems)
    mockListItems.mockImplementation((data) => {
      const response =
        data.caseId === CASE_ID &&
        data.group === "review_required" &&
        data.cursor === null
          ? { ...EMPTY_PAGE, has_more: true, next_cursor: "old-cursor" }
          : EMPTY_PAGE
      return Promise.resolve(response) as unknown as ReturnType<
        typeof inboxListItems
      >
    })
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })

    const { result, rerender } = renderHook(
      ({ caseId }: { caseId: string | null }) =>
        useInbox({ autoRefresh: false, caseId }),
      {
        initialProps: { caseId: CASE_ID } as { caseId: string | null },
        wrapper: createWrapper(queryClient),
      }
    )

    await waitFor(() => {
      const initialGroups = mockListItems.mock.calls.filter(
        ([data]) => data.caseId === CASE_ID && data.cursor === null
      )
      expect(initialGroups).toHaveLength(4)
    })

    act(() => result.current.groups.review_required.loadMore())
    await waitFor(() =>
      expect(mockListItems).toHaveBeenCalledWith(
        expect.objectContaining({
          caseId: CASE_ID,
          cursor: "old-cursor",
          group: "review_required",
        })
      )
    )

    rerender({ caseId: OTHER_CASE_ID })

    await waitFor(() => {
      const nextGroups = mockListItems.mock.calls
        .map(([data]) => data)
        .filter((data) => data.caseId === OTHER_CASE_ID)
      expect(nextGroups).toHaveLength(4)
      expect(new Set(nextGroups.map((data) => data.group))).toEqual(
        new Set(["review_required", "running", "error", "completed"])
      )
      expect(nextGroups.every((data) => data.cursor === null)).toBe(true)
    })

    rerender({ caseId: null })

    await waitFor(() => {
      const unfilteredGroups = mockListItems.mock.calls
        .map(([data]) => data)
        .filter((data) => data.caseId === null)
      expect(unfilteredGroups).toHaveLength(4)
      expect(unfilteredGroups.every((data) => data.cursor === null)).toBe(true)
    })
  })

  it("shows a clearable case filter", () => {
    const onClearCaseFilter = jest.fn()

    render(
      <InboxHeader
        searchQuery=""
        onSearchChange={jest.fn()}
        entityType="all"
        onEntityTypeChange={jest.fn()}
        limit={20}
        onLimitChange={jest.fn()}
        updatedAfter={null}
        onUpdatedAfterChange={jest.fn()}
        createdAfter={null}
        onCreatedAfterChange={jest.fn()}
        caseId={CASE_ID}
        onClearCaseFilter={onClearCaseFilter}
      />
    )

    expect(screen.getByText("Filtered by case")).toBeInTheDocument()
    expect(screen.getByText(CASE_ID.slice(0, 8))).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Clear case filter" }))
    expect(onClearCaseFilter).toHaveBeenCalledTimes(1)
  })

  it("links entitled Inbox readers from a case and hides the action otherwise", () => {
    const { rerender } = render(
      <CaseAgentRunsAction caseId={CASE_ID} workspaceId="workspace-test" />
    )

    expect(
      screen.getByRole("link", { name: "View agent runs" })
    ).toHaveAttribute(
      "href",
      `/workspaces/workspace-test/inbox?caseId=${CASE_ID}`
    )

    mockUseScopeCheck.mockReturnValue(false)
    rerender(
      <CaseAgentRunsAction caseId={CASE_ID} workspaceId="workspace-test" />
    )
    expect(
      screen.queryByRole("link", { name: "View agent runs" })
    ).not.toBeInTheDocument()

    mockUseScopeCheck.mockReturnValue(true)
    mockAgentAddonsEnabled = false
    rerender(
      <CaseAgentRunsAction caseId={CASE_ID} workspaceId="workspace-test" />
    )
    expect(
      screen.queryByRole("link", { name: "View agent runs" })
    ).not.toBeInTheDocument()
  })
})
