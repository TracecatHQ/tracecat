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
import { CaseAgentSessionsPanel } from "@/components/cases/case-agent-sessions-panel"
import { InboxHeader } from "@/components/inbox/inbox-header"
import { useCaseAgentSessions, useInbox } from "@/hooks/use-inbox"
import { getInboxHrefWithoutCaseFilter, parseInboxCaseId } from "@/lib/inbox"
import { QueryClient, QueryClientProvider } from "@/lib/query"

const mockUseScopeCheck = jest.fn<boolean | undefined, [string]>()
let mockCaseAddonsEnabled = true
let mockEntitlementsLoading = false

jest.mock("@/client", () => ({
  approvalsDeleteApproval: jest.fn(),
  inboxListItems: jest.fn(),
  mcpIntegrationsListMcpIntegrations: jest.fn(),
}))

jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: (scope: string) => mockUseScopeCheck(scope),
}))

jest.mock("@/hooks/use-entitlements", () => ({
  useEntitlements: () => ({
    hasEntitlement: (key: string) =>
      key === "case_addons" && mockCaseAddonsEnabled,
    isLoading: mockEntitlementsLoading,
  }),
}))

jest.mock("@/components/inbox/inbox-chat", () => {
  const { useContext, useEffect } = jest.requireActual("react")
  const { TranscriptOutlineContext } = jest.requireActual(
    "@/components/chat/transcript-outline"
  )
  return {
    // Reports a one-entry outline, as the real transcript does.
    InboxChat: ({ session }: { session: { id: string } }) => {
      const report = useContext(TranscriptOutlineContext)
      useEffect(() => {
        report?.([
          {
            id: `prompt-${session.id}`,
            kind: "prompt",
            label: `Prompt of ${session.id}`,
            toolCallCount: 0,
          },
        ])
      }, [report, session.id])
      return <div data-testid="transcript">{session.id}</div>
    },
  }
})

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
const CASE_ITEM: InboxListItemsResponse["items"][number] = {
  id: "inbox-item-1",
  type: "agent_run",
  title: "Case run",
  preview: "",
  status: "completed",
  unread: false,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  source_id: "agent-session-1",
  source_type: "agent_session",
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
    mockCaseAddonsEnabled = true
    mockEntitlementsLoading = false
  })

  it("validates case URL state and preserves unrelated state when clearing", () => {
    expect(parseInboxCaseId(CASE_ID)).toBe(CASE_ID)
    expect(parseInboxCaseId("not-a-case-id")).toBeNull()
    expect(parseInboxCaseId(null)).toBeNull()
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
      if (data.caseId === OTHER_CASE_ID) {
        return new Promise(() => {}) as unknown as ReturnType<
          typeof inboxListItems
        >
      }
      const response =
        data.caseId === CASE_ID &&
        data.group === "review_required" &&
        data.cursor === null
          ? {
              ...EMPTY_PAGE,
              items: [CASE_ITEM],
              has_more: true,
              next_cursor: "old-cursor",
            }
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
    expect(result.current.sessions).toEqual([])
    expect(result.current.isLoading).toBe(true)

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

  it("loads a case's sessions in one request per page", async () => {
    const mockListItems = jest.mocked(inboxListItems)
    mockListItems.mockImplementation((data) => {
      const response = data.cursor
        ? { ...EMPTY_PAGE, items: [{ ...CASE_ITEM, id: "inbox-item-2" }] }
        : {
            items: [CASE_ITEM],
            has_more: true,
            next_cursor: "next-cursor",
          }
      return Promise.resolve(response) as unknown as ReturnType<
        typeof inboxListItems
      >
    })
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })

    const { result, rerender } = renderHook(
      ({ enabled }: { enabled: boolean }) =>
        useCaseAgentSessions({
          caseId: CASE_ID,
          workspaceId: "workspace-test",
          enabled,
        }),
      {
        initialProps: { enabled: false },
        wrapper: createWrapper(queryClient),
      }
    )
    expect(mockListItems).not.toHaveBeenCalled()

    rerender({ enabled: true })
    await waitFor(() => expect(result.current.sessions).toHaveLength(1))
    expect(mockListItems).toHaveBeenCalledTimes(1)
    expect(mockListItems.mock.calls[0][0]).toEqual(
      expect.objectContaining({ caseId: CASE_ID, cursor: null })
    )
    expect(mockListItems.mock.calls[0][0].group).toBeUndefined()
    expect(result.current.hasMore).toBe(true)

    act(() => result.current.loadMore())
    await waitFor(() => expect(result.current.sessions).toHaveLength(2))
    expect(mockListItems).toHaveBeenCalledTimes(2)
    expect(mockListItems).toHaveBeenLastCalledWith(
      expect.objectContaining({ caseId: CASE_ID, cursor: "next-cursor" })
    )
    expect(result.current.hasMore).toBe(false)
  })

  it("lists a case's sessions and mounts only the selected transcript", async () => {
    jest.mocked(inboxListItems).mockResolvedValue({
      ...EMPTY_PAGE,
      items: [
        {
          ...CASE_ITEM,
          metadata: {
            tools: ["core__cases__create_comment", "core__cases__get_case"],
            mcp_integrations: [],
          },
        },
        {
          ...CASE_ITEM,
          id: "inbox-item-2",
          source_id: "agent-session-2",
          title: "Older run",
        },
      ],
    } as never)
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })

    render(
      <CaseAgentSessionsPanel caseId={CASE_ID} workspaceId="workspace-test" />,
      { wrapper: createWrapper(queryClient) }
    )

    // The newest session is selected until the user picks another.
    const newest = await screen.findByRole("button", { name: /Case run/ })
    expect(newest).toHaveAttribute("aria-current", "true")
    expect(screen.getAllByTestId("transcript")).toHaveLength(1)
    expect(screen.getByTestId("transcript")).toHaveTextContent(
      "agent-session-1"
    )

    // One icon per tool namespace, and the selected session's outline.
    expect(screen.getAllByTitle("core.cases")).toHaveLength(1)
    expect(
      screen.getByRole("button", { name: /Prompt of agent-session-1/ })
    ).toBeInTheDocument()

    const older = screen.getByRole("button", { name: /Older run/ })
    fireEvent.click(older)
    expect(older).toHaveAttribute("aria-current", "true")
    expect(newest).not.toHaveAttribute("aria-current")
    expect(screen.getAllByTestId("transcript")).toHaveLength(1)
    expect(screen.getByTestId("transcript")).toHaveTextContent(
      "agent-session-2"
    )
    expect(
      screen.queryByRole("button", { name: /Prompt of agent-session-1/ })
    ).not.toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: /Prompt of agent-session-2/ })
    ).toBeInTheDocument()

    // The chevron collapses the selected session's outline.
    fireEvent.click(screen.getByRole("button", { name: "Collapse session" }))
    expect(
      screen.queryByRole("button", { name: /Prompt of agent-session-2/ })
    ).not.toBeInTheDocument()
  })

  it("does not request sessions without the scope or entitlement", () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    mockUseScopeCheck.mockReturnValue(false)
    const { unmount } = render(
      <CaseAgentSessionsPanel caseId={CASE_ID} workspaceId="workspace-test" />,
      { wrapper: createWrapper(queryClient) }
    )
    expect(screen.getByRole("status")).toHaveTextContent(/cannot view/)
    unmount()

    mockUseScopeCheck.mockReturnValue(true)
    mockCaseAddonsEnabled = false
    render(
      <CaseAgentSessionsPanel caseId={CASE_ID} workspaceId="workspace-test" />,
      { wrapper: createWrapper(queryClient) }
    )
    expect(screen.getByRole("status")).toHaveTextContent(/Enterprise/)
    expect(inboxListItems).not.toHaveBeenCalled()
  })
})
