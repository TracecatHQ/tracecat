/**
 * @jest-environment jsdom
 */

import { renderHook, waitFor } from "@testing-library/react"
import type { ReactNode } from "react"
import type { CaseLinkedTableRead } from "@/client"
import {
  casesBatchLinkCaseRows,
  casesBatchUnlinkCaseRows,
  casesListCaseLinkedTables,
} from "@/client"
import {
  caseRowsQueryKey,
  useCaseLinkedTables,
  useLinkCaseRows,
  useUnlinkCaseRows,
} from "@/hooks/use-case-rows"
import { QueryClient, QueryClientProvider } from "@/lib/query"

jest.mock("@/client", () => {
  const actual = jest.requireActual("@/client")
  return {
    ...actual,
    casesBatchLinkCaseRows: jest.fn(),
    casesBatchUnlinkCaseRows: jest.fn(),
    casesListCaseLinkedTables: jest.fn(),
  }
})

const mockBatchLink = casesBatchLinkCaseRows as jest.MockedFunction<
  typeof casesBatchLinkCaseRows
>
const mockBatchUnlink = casesBatchUnlinkCaseRows as jest.MockedFunction<
  typeof casesBatchUnlinkCaseRows
>
const mockListLinkedTables = casesListCaseLinkedTables as jest.MockedFunction<
  typeof casesListCaseLinkedTables
>

const SCOPE = { caseId: "case-1", workspaceId: "ws-1" }
const ROW_IDS = Array.from({ length: 450 }, (_, index) => `row-${index}`)

function setup() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })
  const invalidateSpy = jest.spyOn(queryClient, "invalidateQueries")
  function wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    )
  }
  return { wrapper, invalidateSpy }
}

describe("caseRowsQueryKey", () => {
  it("prefixes every case-rows query", () => {
    expect(caseRowsQueryKey("case-1")).toEqual(["case-rows", "case-1"])
  })
})

describe("useLinkCaseRows", () => {
  beforeEach(() => {
    jest.clearAllMocks()
  })

  it("chunks ids into batches of 200, sums counts and invalidates", async () => {
    mockBatchLink
      .mockResolvedValueOnce({ linked_count: 200, already_linked_count: 0 })
      .mockResolvedValueOnce({ linked_count: 150, already_linked_count: 50 })
      .mockResolvedValueOnce({ linked_count: 40, already_linked_count: 10 })
    const { wrapper, invalidateSpy } = setup()

    const { result } = renderHook(() => useLinkCaseRows(SCOPE), { wrapper })
    const outcome = await result.current.linkCaseRows({
      tableId: "table-1",
      rowIds: ROW_IDS,
    })

    expect(outcome).toEqual({ linkedCount: 390, alreadyLinkedCount: 60 })
    expect(mockBatchLink).toHaveBeenCalledTimes(3)
    const batches = mockBatchLink.mock.calls.map(
      ([params]) => params.requestBody.row_ids
    )
    expect(batches.map((batch) => batch.length)).toEqual([200, 200, 50])
    expect(batches.flat()).toEqual(ROW_IDS)
    for (const [params] of mockBatchLink.mock.calls) {
      expect(params).toEqual(
        expect.objectContaining({
          caseId: "case-1",
          workspaceId: "ws-1",
          requestBody: expect.objectContaining({ table_id: "table-1" }),
        })
      )
    }
    await waitFor(() => {
      expect(invalidateSpy).toHaveBeenCalledWith({
        queryKey: ["case-rows", "case-1"],
      })
    })
  })

  it("rejects with the API error so the caller can toast it", async () => {
    const failure = new Error("boom")
    mockBatchLink.mockRejectedValueOnce(failure)
    const { wrapper } = setup()

    const { result } = renderHook(() => useLinkCaseRows(SCOPE), { wrapper })

    await expect(
      result.current.linkCaseRows({ tableId: "table-1", rowIds: ["row-1"] })
    ).rejects.toBe(failure)
  })
})

describe("useUnlinkCaseRows", () => {
  beforeEach(() => {
    jest.clearAllMocks()
  })

  it("chunks ids into batches of 200, sums counts and invalidates", async () => {
    mockBatchUnlink
      .mockResolvedValueOnce({ unlinked_count: 200 })
      .mockResolvedValueOnce({ unlinked_count: 199 })
      .mockResolvedValueOnce({ unlinked_count: 50 })
    const { wrapper, invalidateSpy } = setup()

    const { result } = renderHook(() => useUnlinkCaseRows(SCOPE), { wrapper })
    const outcome = await result.current.unlinkCaseRows({
      tableId: "table-1",
      rowIds: ROW_IDS,
    })

    expect(outcome).toEqual({ unlinkedCount: 449 })
    expect(mockBatchUnlink).toHaveBeenCalledTimes(3)
    const batches = mockBatchUnlink.mock.calls.map(
      ([params]) => params.requestBody.row_ids
    )
    expect(batches.map((batch) => batch.length)).toEqual([200, 200, 50])
    expect(batches.flat()).toEqual(ROW_IDS)
    for (const [params] of mockBatchUnlink.mock.calls) {
      expect(params.requestBody.table_id).toBe("table-1")
    }
    await waitFor(() => {
      expect(invalidateSpy).toHaveBeenCalledWith({
        queryKey: ["case-rows", "case-1"],
      })
    })
  })
})

describe("useCaseLinkedTables", () => {
  beforeEach(() => {
    jest.clearAllMocks()
  })

  it("returns the linked-tables summary", async () => {
    const summary: CaseLinkedTableRead[] = [
      { table_id: "table-1", table_name: "Alerts", row_count: 3 },
      { table_id: "table-2", table_name: null, row_count: 1 },
    ]
    mockListLinkedTables.mockResolvedValueOnce(summary)
    const { wrapper } = setup()

    const { result } = renderHook(() => useCaseLinkedTables(SCOPE), {
      wrapper,
    })

    await waitFor(() => {
      expect(result.current.linkedTablesIsLoading).toBe(false)
    })
    expect(result.current.linkedTables).toEqual(summary)
    expect(result.current.linkedTablesError).toBeNull()
    expect(mockListLinkedTables).toHaveBeenCalledWith(SCOPE)
  })
})
