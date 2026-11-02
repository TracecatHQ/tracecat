import { act, renderHook, waitFor } from "@testing-library/react"
import type { ReactNode } from "react"
import {
  type CaseReadMinimal,
  type CasesSearchCasesResponse,
  casesSearchCases,
} from "@/client"
import { CancelablePromise } from "@/client/core/CancelablePromise"
import { useCaseSearchInfinite } from "@/hooks/use-case-search-infinite"
import { QueryClient, QueryClientProvider } from "@/lib/query"

jest.mock("@/client", () => ({ casesSearchCases: jest.fn() }))

const WORKSPACE_ID = "11111111-1111-4111-8111-111111111111"
const PARENT_ID = "00000000-0000-4000-8000-000000000001"
const CASE: CaseReadMinimal = {
  id: "00000000-0000-4000-8000-000000000042",
  short_id: "CASE-0042",
  created_at: "2024-01-01T00:00:00Z",
  updated_at: "2024-01-01T00:00:00Z",
  summary: "Synthetic sub-case",
  status: "new",
  priority: "low",
  severity: "low",
  dropdown_values: [],
  num_sub_cases: 0,
}

it("opts out of population counts on sub-case initial and continuation pages", async () => {
  jest.mocked(casesSearchCases).mockImplementation(
    (request) =>
      new CancelablePromise<CasesSearchCasesResponse>((resolve) =>
        resolve({
          items: [
            {
              ...CASE,
              id: request.cursor
                ? "00000000-0000-4000-8000-000000000043"
                : CASE.id,
            },
          ],
          next_cursor: request.cursor ? null : "synthetic-sub-case-cursor",
          has_more: !request.cursor,
          total_estimate: null,
        })
      )
  )
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    )
  }
  const { result } = renderHook(
    () =>
      useCaseSearchInfinite({
        workspaceId: WORKSPACE_ID,
        parentId: PARENT_ID,
        pageSize: 1,
      }),
    { wrapper: Wrapper }
  )
  await waitFor(() => expect(result.current.items).toHaveLength(1))
  expect(casesSearchCases).toHaveBeenLastCalledWith(
    expect.objectContaining({
      workspaceId: WORKSPACE_ID,
      parentId: PARENT_ID,
      includeCount: false,
      cursor: undefined,
    })
  )
  await act(async () => {
    await result.current.fetchNextPage()
  })
  await waitFor(() => expect(result.current.items).toHaveLength(2))
  expect(casesSearchCases).toHaveBeenCalledTimes(2)
  expect(casesSearchCases).toHaveBeenLastCalledWith(
    expect.objectContaining({
      includeCount: false,
      cursor: "synthetic-sub-case-cursor",
    })
  )
})
