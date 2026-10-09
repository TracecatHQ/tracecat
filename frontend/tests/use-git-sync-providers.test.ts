/**
 * @jest-environment jsdom
 */

import { renderHook } from "@testing-library/react"
import { useGitSyncProviders } from "@/hooks/use-git-sync-providers"

type QueryState = { data?: unknown; isLoading: boolean; isError: boolean }
let mockQueries: Record<string, QueryState> = {}

jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: () => true,
}))

jest.mock("@/lib/query", () => ({
  useQuery: ({ queryKey }: { queryKey: string[] }) =>
    mockQueries[queryKey[0]] ?? { isLoading: false, isError: false },
}))

const ok = (data: unknown): QueryState => ({
  data,
  isLoading: false,
  isError: false,
})
const failed: QueryState = { isLoading: false, isError: true }

beforeEach(() => {
  mockQueries = {}
})

describe("useGitSyncProviders", () => {
  it("keeps working providers when another status check fails", () => {
    mockQueries = {
      "github-app-credentials-status": failed,
      "gitlab-token-credentials-status": ok({
        exists: true,
        base_url: "https://gitlab.example.com",
      }),
      "bitbucket-token-credentials-status": ok({ exists: false }),
      "bitbucket-data-center-token-credentials-status": ok({ exists: false }),
    }

    const { result } = renderHook(() => useGitSyncProviders())

    expect(result.current).toEqual({
      kind: "known",
      providers: [{ id: "gitlab", host: "gitlab.example.com" }],
    })
  })

  it("reports unknown when a check fails and none is confirmed", () => {
    mockQueries = {
      "github-app-credentials-status": failed,
      "gitlab-token-credentials-status": ok({ exists: false }),
      "bitbucket-token-credentials-status": ok({ exists: false }),
      "bitbucket-data-center-token-credentials-status": ok({ exists: false }),
    }

    const { result } = renderHook(() => useGitSyncProviders())

    expect(result.current).toEqual({ kind: "unknown" })
  })

  it("reports none set up only when every check succeeded", () => {
    mockQueries = {
      "github-app-credentials-status": ok({ exists: false }),
      "gitlab-token-credentials-status": ok({ exists: false }),
      "bitbucket-token-credentials-status": ok({ exists: false }),
      "bitbucket-data-center-token-credentials-status": ok({ exists: false }),
    }

    const { result } = renderHook(() => useGitSyncProviders())

    expect(result.current).toEqual({ kind: "known", providers: [] })
  })
})
