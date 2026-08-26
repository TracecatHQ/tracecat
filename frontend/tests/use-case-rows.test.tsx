/**
 * @jest-environment jsdom
 */

import { renderHook, waitFor } from "@testing-library/react"
import type { ReactNode } from "react"
import type { CaseLinkedTableRead, CaseTableRowRead } from "@/client"
import {
  casesBatchLinkCaseRows,
  casesBatchUnlinkCaseRows,
  casesListCaseLinkedTables,
  casesListCaseRows,
} from "@/client"
import {
  CaseRowsLinkError,
  CaseRowsUnlinkError,
  caseRowsQueryKey,
  useCaseLinkedTables,
  useCaseTableRows,
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
    casesListCaseRows: jest.fn(),
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
const mockListCaseRows = casesListCaseRows as jest.MockedFunction<
  typeof casesListCaseRows
>

const SCOPE = { caseId: "case-1", workspaceId: "ws-1" }
const ROW_IDS = Array.from({ length: 450 }, (_, index) => `row-${index}`)
const TWO_CHUNK_ROW_IDS = ROW_IDS.slice(0, 250)

/** Await a link that must reject with the hook's partial-failure error. */
async function captureLinkError(
  promise: Promise<unknown>
): Promise<CaseRowsLinkError> {
  try {
    await promise
  } catch (error) {
    if (error instanceof CaseRowsLinkError) {
      return error
    }
    throw error
  }
  throw new Error("Expected the link to reject")
}

/** Await an unlink that must reject with the hook's partial-failure error. */
async function captureUnlinkError(
  promise: Promise<unknown>
): Promise<CaseRowsUnlinkError> {
  try {
    await promise
  } catch (error) {
    if (error instanceof CaseRowsUnlinkError) {
      return error
    }
    throw error
  }
  throw new Error("Expected the unlink to reject")
}

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

  it("reports what committed when a later chunk fails", async () => {
    const failure = new Error("boom")
    mockBatchLink
      .mockResolvedValueOnce({ linked_count: 199, already_linked_count: 1 })
      .mockRejectedValueOnce(failure)
    const { wrapper } = setup()

    const { result } = renderHook(() => useLinkCaseRows(SCOPE), { wrapper })
    const error = await captureLinkError(
      result.current.linkCaseRows({
        tableId: "table-1",
        rowIds: TWO_CHUNK_ROW_IDS,
      })
    )

    expect(error.linkedCount).toBe(199)
    expect(error.alreadyLinkedCount).toBe(1)
    expect(error.committedRowIds).toEqual(TWO_CHUNK_ROW_IDS.slice(0, 200))
    expect(error.cause).toBe(failure)
    expect(mockBatchLink).toHaveBeenCalledTimes(2)
  })

  it("reports nothing committed when the first chunk fails", async () => {
    const failure = new Error("boom")
    mockBatchLink.mockRejectedValueOnce(failure)
    const { wrapper } = setup()

    const { result } = renderHook(() => useLinkCaseRows(SCOPE), { wrapper })
    const error = await captureLinkError(
      result.current.linkCaseRows({ tableId: "table-1", rowIds: ["row-1"] })
    )

    expect(error.linkedCount).toBe(0)
    expect(error.alreadyLinkedCount).toBe(0)
    expect(error.committedRowIds).toEqual([])
    expect(error.cause).toBe(failure)
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

  it("reports what committed when a later chunk fails", async () => {
    const failure = new Error("boom")
    mockBatchUnlink
      .mockResolvedValueOnce({ unlinked_count: 200 })
      .mockRejectedValueOnce(failure)
    const { wrapper } = setup()

    const { result } = renderHook(() => useUnlinkCaseRows(SCOPE), { wrapper })
    const error = await captureUnlinkError(
      result.current.unlinkCaseRows({
        tableId: "table-1",
        rowIds: TWO_CHUNK_ROW_IDS,
      })
    )

    expect(error.unlinkedCount).toBe(200)
    expect(error.committedRowIds).toEqual(TWO_CHUNK_ROW_IDS.slice(0, 200))
    expect(error.cause).toBe(failure)
    expect(mockBatchUnlink).toHaveBeenCalledTimes(2)
  })

  it("reports nothing committed when the first chunk fails", async () => {
    const failure = new Error("boom")
    mockBatchUnlink.mockRejectedValueOnce(failure)
    const { wrapper } = setup()

    const { result } = renderHook(() => useUnlinkCaseRows(SCOPE), { wrapper })
    const error = await captureUnlinkError(
      result.current.unlinkCaseRows({ tableId: "table-1", rowIds: ["row-1"] })
    )

    expect(error.unlinkedCount).toBe(0)
    expect(error.committedRowIds).toEqual([])
    expect(error.cause).toBe(failure)
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

function makeRow(rowId: string): CaseTableRowRead {
  return {
    id: `link-${rowId}`,
    case_id: "case-1",
    table_id: "table-1",
    table_name: "Alerts",
    row_id: rowId,
    row_data: { name: rowId },
    is_row_available: true,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  }
}

function makePage(
  items: CaseTableRowRead[],
  hasMore: boolean,
  nextCursor: string | null = null
) {
  return {
    items,
    next_cursor: nextCursor,
    prev_cursor: null,
    has_more: hasMore,
    has_previous: false,
    total_estimate: items.length,
  }
}

describe("useCaseTableRows", () => {
  beforeEach(() => {
    jest.clearAllMocks()
  })

  it("returns a single page's items", async () => {
    const rows = [makeRow("r1"), makeRow("r2")]
    mockListCaseRows.mockResolvedValueOnce(makePage(rows, false))
    const { wrapper } = setup()

    const { result } = renderHook(
      () => useCaseTableRows({ ...SCOPE, tableId: "table-1" }),
      { wrapper }
    )

    await waitFor(() => {
      expect(result.current.caseTableRowsIsLoading).toBe(false)
    })
    expect(result.current.caseTableRows).toEqual(rows)
    expect(result.current.caseTableRowsError).toBeNull()
    expect(mockListCaseRows).toHaveBeenCalledTimes(1)
    expect(mockListCaseRows).toHaveBeenCalledWith(
      expect.objectContaining({
        caseId: "case-1",
        workspaceId: "ws-1",
        tableId: "table-1",
        limit: 200,
      })
    )
  })

  it("follows the cursor and concatenates every page in order", async () => {
    const firstPage = [makeRow("r1"), makeRow("r2")]
    const secondPage = [makeRow("r3")]
    mockListCaseRows
      .mockResolvedValueOnce(makePage(firstPage, true, "c2"))
      .mockResolvedValueOnce(makePage(secondPage, false))
    const { wrapper } = setup()

    const { result } = renderHook(
      () => useCaseTableRows({ ...SCOPE, tableId: "table-1" }),
      { wrapper }
    )

    await waitFor(() => {
      expect(result.current.caseTableRowsIsLoading).toBe(false)
    })
    expect(result.current.caseTableRows).toEqual([...firstPage, ...secondPage])
    expect(mockListCaseRows).toHaveBeenCalledTimes(2)
    const [[firstParams], [secondParams]] = mockListCaseRows.mock.calls
    expect(firstParams.cursor).toBeNull()
    expect(secondParams.cursor).toBe("c2")
    for (const [params] of mockListCaseRows.mock.calls) {
      expect(params).toEqual(
        expect.objectContaining({
          caseId: "case-1",
          workspaceId: "ws-1",
          tableId: "table-1",
          limit: 200,
        })
      )
    }
  })
})
