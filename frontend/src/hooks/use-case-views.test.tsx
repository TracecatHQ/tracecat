import { renderHook, waitFor } from "@testing-library/react"
import type { ReactNode } from "react"
import {
  type CaseViewRead,
  caseViewsListBuiltinCaseViews,
  caseViewsListCaseViews,
} from "@/client"
import { useCaseViews } from "@/hooks/use-case-views"
import { QueryClient, QueryClientProvider } from "@/lib/query"

jest.mock("@/client", () => ({
  caseViewsListBuiltinCaseViews: jest.fn(),
  caseViewsListCaseViews: jest.fn(),
}))

const listBuiltins = jest.mocked(caseViewsListBuiltinCaseViews)
const listViews = jest.mocked(caseViewsListCaseViews)

function view(id: string, overrides: Partial<CaseViewRead> = {}): CaseViewRead {
  return {
    id,
    kind: "custom",
    name: `View ${id}`,
    visibility: "workspace",
    query: {},
    can_edit: true,
    ...overrides,
  }
}

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

describe("useCaseViews", () => {
  beforeEach(() => {
    jest.clearAllMocks()
    listBuiltins.mockResolvedValue([
      view("assigned_to_me", { kind: "builtin" }),
    ])
  })

  it("follows the cursor through every page, in order", async () => {
    listViews
      .mockResolvedValueOnce({
        items: [view("a"), view("b", { pinned: true, pin_position: 1 })],
        has_more: true,
        next_cursor: "page-2",
      })
      .mockResolvedValueOnce({
        items: [view("c", { pinned: true, pin_position: 0 })],
        has_more: false,
        next_cursor: null,
      })

    const { result } = renderHook(() => useCaseViews("workspace-1"), {
      wrapper,
    })

    await waitFor(() => expect(result.current.shared).toHaveLength(3))
    expect(result.current.shared.map((item) => item.id)).toEqual([
      "a",
      "b",
      "c",
    ])
    expect(result.current.pinned.map((item) => item.id)).toEqual(["c", "b"])
    expect(listViews.mock.calls.map(([args]) => args.cursor)).toEqual([
      undefined,
      "page-2",
    ])
  })

  it("returns nothing and fetches nothing without case access", () => {
    const { result } = renderHook(() => useCaseViews("workspace-1", false), {
      wrapper,
    })

    expect(result.current.builtins).toEqual([])
    expect(result.current.shared).toEqual([])
    expect(listViews).not.toHaveBeenCalled()
  })
})
