import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { renderHook } from "@testing-library/react"
import type { ReactNode } from "react"
import { casesBatchDeleteCases, casesBatchUpdateCases } from "@/client"
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

  it("updates cases once and invalidates the cases cache once", async () => {
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
    expect(invalidateQueries).toHaveBeenCalledTimes(1)
    expect(invalidateQueries).toHaveBeenCalledWith({
      queryKey: ["cases"],
      exact: false,
    })
  })

  it("deletes cases once and invalidates the cases cache once", async () => {
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
    expect(invalidateQueries).toHaveBeenCalledTimes(1)
    expect(invalidateQueries).toHaveBeenCalledWith({
      queryKey: ["cases"],
      exact: false,
    })
  })
})
