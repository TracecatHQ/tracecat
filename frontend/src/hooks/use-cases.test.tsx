import { act, renderHook } from "@testing-library/react"
import type { ReactNode } from "react"
import { casesSearchCaseAggregates, casesSearchCases } from "@/client"
import { useCases } from "@/hooks/use-cases"
import { QueryClient, QueryClientProvider } from "@/lib/query"

jest.mock("@/client", () => ({
  casesSearchCases: jest.fn(),
  casesSearchCaseAggregates: jest.fn(),
}))

jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: () => "workspace-test",
}))

const searchCases = jest.mocked(casesSearchCases)
const searchAggregates = jest.mocked(casesSearchCaseAggregates)

function setup() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  )
  return renderHook(() => useCases({ autoRefresh: false }), { wrapper })
}

async function advance(ms: number) {
  await act(async () => {
    await jest.advanceTimersByTimeAsync(ms)
  })
}

function searchTerms(mock: jest.Mock) {
  return mock.mock.calls.map(([params]) => params.searchTerm)
}

beforeEach(() => {
  jest.useFakeTimers()
  jest.clearAllMocks()
  window.localStorage.clear()
  searchCases.mockResolvedValue({
    items: [],
    has_more: false,
    next_cursor: null,
  } as unknown as Awaited<ReturnType<typeof casesSearchCases>>)
  searchAggregates.mockResolvedValue(
    {} as Awaited<ReturnType<typeof casesSearchCaseAggregates>>
  )
})

afterEach(() => jest.useRealTimers())

it("debounces search requests while typing", async () => {
  const { result } = setup()
  await advance(1)
  expect(searchTerms(searchCases)).toEqual([undefined])

  for (const query of ["p", "po", "pow", "powershell"]) {
    act(() => result.current.setSearchQuery(query))
    await advance(100)
  }
  expect(searchTerms(searchCases)).toEqual([undefined])
  expect(searchTerms(searchAggregates)).toEqual([undefined])

  await advance(300)
  expect(searchTerms(searchCases)).toEqual([undefined, "powershell"])
  expect(searchTerms(searchAggregates)).toEqual([undefined, "powershell"])
  expect(result.current.filters.searchQuery).toBe("powershell")
})

it("applies a cleared search immediately", async () => {
  const { result } = setup()
  await advance(1)
  act(() => result.current.setSearchQuery("phishing"))
  await advance(300)
  expect(searchTerms(searchCases)).toEqual([undefined, "phishing"])

  searchCases.mockClear()
  act(() => result.current.setSearchQuery(""))
  await advance(1)
  expect(searchTerms(searchCases)).toEqual([undefined])

  act(() => result.current.setSearchQuery("mal"))
  await advance(100)
  expect(searchTerms(searchCases)).toEqual([undefined])
  await advance(300)
  expect(searchTerms(searchCases)).toEqual([undefined, "mal"])
})
