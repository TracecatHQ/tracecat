import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { renderHook } from "@testing-library/react"
import type { ReactNode } from "react"
import { casesBatchDeleteCases, casesBatchUpdateCases } from "@/client"
import { chunkCaseIds } from "@/components/cases/cases-layout"
import { useBatchDeleteCases, useBatchUpdateCases } from "@/lib/hooks"

jest.mock("@/client", () => {
  const actual = jest.requireActual("@/client")
  return {
    ...actual,
    casesBatchDeleteCases: jest.fn(),
    casesBatchUpdateCases: jest.fn(),
  }
})

const mockBatchDeleteCases = casesBatchDeleteCases as jest.MockedFunction<
  typeof casesBatchDeleteCases
>
const mockBatchUpdateCases = casesBatchUpdateCases as jest.MockedFunction<
  typeof casesBatchUpdateCases
>

describe("batch case hooks", () => {
  let queryClient: QueryClient

  beforeEach(() => {
    queryClient = new QueryClient({
      defaultOptions: {
        mutations: { retry: false },
        queries: { retry: false },
      },
    })
    jest.clearAllMocks()
  })

  function wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    )
  }

  it("chunks case IDs at the server batch limit", () => {
    const caseIds = Array.from({ length: 2001 }, (_, index) => `case-${index}`)

    const chunks = chunkCaseIds(caseIds)

    expect(chunks.map((chunk) => chunk.length)).toEqual([1000, 1000, 1])
    expect(chunks.flat()).toEqual(caseIds)
  })

  it("updates cases once without invalidating the cases cache", async () => {
    mockBatchUpdateCases.mockResolvedValue({
      results: [{ case_id: "case-1", success: true, error: null }],
      succeeded: 1,
      failed: 0,
    })
    const invalidateQueries = jest.spyOn(queryClient, "invalidateQueries")
    const { result } = renderHook(
      () => useBatchUpdateCases({ workspaceId: "workspace-1" }),
      { wrapper }
    )

    await result.current.batchUpdateCases({
      case_ids: ["case-1"],
      update: { summary: "Updated" },
    })

    expect(mockBatchUpdateCases).toHaveBeenCalledTimes(1)
    expect(mockBatchUpdateCases).toHaveBeenCalledWith({
      workspaceId: "workspace-1",
      requestBody: {
        case_ids: ["case-1"],
        update: { summary: "Updated" },
      },
    })
    // Invalidation is owned by the caller (one refetch per bulk operation,
    // not per 1000-ID chunk) — the hook itself must not invalidate.
    expect(invalidateQueries).not.toHaveBeenCalled()
  })

  it("deletes cases once without invalidating the cases cache", async () => {
    mockBatchDeleteCases.mockResolvedValue({
      results: [{ case_id: "case-1", success: true, error: null }],
      succeeded: 1,
      failed: 0,
    })
    const invalidateQueries = jest.spyOn(queryClient, "invalidateQueries")
    const { result } = renderHook(
      () => useBatchDeleteCases({ workspaceId: "workspace-1" }),
      { wrapper }
    )

    await result.current.batchDeleteCases({ case_ids: ["case-1"] })

    expect(mockBatchDeleteCases).toHaveBeenCalledTimes(1)
    expect(mockBatchDeleteCases).toHaveBeenCalledWith({
      workspaceId: "workspace-1",
      requestBody: { case_ids: ["case-1"] },
    })
    // Invalidation is owned by the caller (one refetch per bulk operation,
    // not per 1000-ID chunk) — the hook itself must not invalidate.
    expect(invalidateQueries).not.toHaveBeenCalled()
  })
})
