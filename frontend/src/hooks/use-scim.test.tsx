import { act, renderHook, waitFor } from "@testing-library/react"
import type { ReactNode } from "react"
import {
  scimGetScimConnection,
  scimListExternalGroups,
  scimListScimMappings,
} from "@/client"
import {
  useScimConnection,
  useScimExternalGroups,
  useScimMappings,
} from "@/hooks/use-scim"
import { QueryClient, QueryClientProvider } from "@/lib/query"

jest.mock("@/client", () => ({
  scimGetScimConnection: jest.fn(),
  scimIssueScimToken: jest.fn(),
  scimRevokeScimToken: jest.fn(),
  scimListExternalGroups: jest.fn(),
  scimListScimMappings: jest.fn(),
  scimCreateScimMapping: jest.fn(),
  scimDeleteScimMapping: jest.fn(),
}))

jest.mock("@/components/ui/use-toast", () => ({
  toast: jest.fn(),
}))

function createWrapper(queryClient: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    )
  }
}

function createQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })
}

describe("useScimConnection", () => {
  beforeEach(() => {
    jest.mocked(scimGetScimConnection).mockReset()
  })

  it("treats a 404 as not configured rather than an error", async () => {
    jest
      .mocked(scimGetScimConnection)
      .mockRejectedValue(Object.assign(new Error("Not Found"), { status: 404 }))

    const { result } = renderHook(() => useScimConnection(), {
      wrapper: createWrapper(createQueryClient()),
    })

    await waitFor(() => expect(result.current.connectionIsLoading).toBe(false))
    expect(result.current.connection).toBeNull()
    expect(result.current.connectionError).toBeNull()
  })

  it("surfaces a non-404 failure as a query error", async () => {
    jest
      .mocked(scimGetScimConnection)
      .mockRejectedValue(Object.assign(new Error("Forbidden"), { status: 403 }))

    const { result } = renderHook(() => useScimConnection(), {
      wrapper: createWrapper(createQueryClient()),
    })

    await waitFor(() => expect(result.current.connectionError).not.toBeNull())
    expect(result.current.connectionError?.status).toBe(403)
    expect(result.current.connection).toBeUndefined()
  })
})

it("loads external groups one page at a time", async () => {
  const listGroups = jest.mocked(scimListExternalGroups)
  listGroups.mockReset()
  listGroups
    .mockResolvedValueOnce({
      items: [
        {
          id: "first",
          external_id: "one",
          display_name: "One",
          member_count: 0,
        },
      ],
      next_cursor: "next-page",
      prev_cursor: null,
    })
    .mockResolvedValueOnce({
      items: [
        {
          id: "second",
          external_id: "two",
          display_name: "Two",
          member_count: 0,
        },
      ],
      next_cursor: null,
      prev_cursor: "previous-page",
    })
  const { result } = renderHook(() => useScimExternalGroups(), {
    wrapper: createWrapper(createQueryClient()),
  })
  await waitFor(() => expect(result.current.externalGroups).toHaveLength(1))
  expect(listGroups).toHaveBeenCalledTimes(1)
  expect(listGroups).toHaveBeenLastCalledWith({ limit: 50, cursor: undefined })
  await act(async () => {
    await result.current.fetchNextExternalGroups()
  })
  await waitFor(() => expect(result.current.externalGroups).toHaveLength(2))
  expect(listGroups).toHaveBeenLastCalledWith({
    limit: 50,
    cursor: "next-page",
  })
  expect(result.current.externalGroupsHasNextPage).toBe(false)
})

it("loads mappings only when requested and keeps existing pages after a failure", async () => {
  const listMappings = jest.mocked(scimListScimMappings)
  const mapping = {
    id: "first",
    external_group_id: "source",
    external_group_external_id: "idp-source",
    external_group_display_name: "IdP team",
    group_id: "target",
    group_name: "Target team",
  }
  listMappings.mockReset()
  listMappings
    .mockResolvedValueOnce({ items: [mapping], next_cursor: "next-page" })
    .mockRejectedValueOnce(new Error("Unavailable"))
    .mockResolvedValueOnce({
      items: [{ ...mapping, id: "second" }],
      next_cursor: null,
    })
  const { result } = renderHook(() => useScimMappings(), {
    wrapper: createWrapper(createQueryClient()),
  })
  await waitFor(() => expect(result.current.mappings).toHaveLength(1))
  expect(listMappings).toHaveBeenCalledTimes(1)
  expect(listMappings).toHaveBeenLastCalledWith({
    limit: 50,
    cursor: undefined,
  })
  await act(async () => {
    await result.current.fetchNextMappings()
  })
  await waitFor(() => expect(result.current.mappingsError).not.toBeNull())
  expect(result.current.mappings).toEqual([mapping])
  await act(async () => {
    await result.current.fetchNextMappings()
  })
  await waitFor(() => expect(result.current.mappings).toHaveLength(2))
  expect(listMappings).toHaveBeenLastCalledWith({
    limit: 50,
    cursor: "next-page",
  })
  expect(result.current.mappingsHasNextPage).toBe(false)
})
