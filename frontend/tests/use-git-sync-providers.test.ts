/**
 * @jest-environment jsdom
 */

import { renderHook } from "@testing-library/react"
import { useGitSyncProviders } from "@/hooks/use-git-sync-providers"

type QueryState = { data?: unknown; isLoading: boolean; isError: boolean }
let mockQueries: Record<string, QueryState> = {}
let mockCanReadOrgSettings: boolean | undefined = true

jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: (scope: string) =>
    scope === "org:settings:read" ? mockCanReadOrgSettings : false,
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
const loading: QueryState = { isLoading: true, isError: false }
const none = ok({ exists: false })

function allNone(): Record<string, QueryState> {
  return {
    "github-app-credentials-status": none,
    "gitlab-token-credentials-status": none,
    "bitbucket-token-credentials-status": none,
    "bitbucket-data-center-token-credentials-status": none,
  }
}

beforeEach(() => {
  mockQueries = allNone()
  mockCanReadOrgSettings = true
})

describe("useGitSyncProviders", () => {
  it("keeps a provider whose check failed selectable, without a host", () => {
    mockQueries = {
      ...allNone(),
      // Stale data on an errored query must not count as confirmed.
      "github-app-credentials-status": {
        ...failed,
        data: { exists: true },
      },
      "gitlab-token-credentials-status": ok({
        exists: true,
        base_url: "https://gitlab.example.com",
      }),
    }

    const { result } = renderHook(() => useGitSyncProviders())

    expect(result.current).toEqual({
      kind: "known",
      providers: [
        { id: "github" },
        { id: "gitlab", host: "gitlab.example.com" },
      ],
    })
  })

  it("reports unknown when every check fails", () => {
    mockQueries = {
      "github-app-credentials-status": failed,
      "gitlab-token-credentials-status": failed,
      "bitbucket-token-credentials-status": failed,
      "bitbucket-data-center-token-credentials-status": failed,
    }

    const { result } = renderHook(() => useGitSyncProviders())

    expect(result.current).toEqual({ kind: "unknown" })
  })

  it("reports none set up only when every check succeeded", () => {
    const { result } = renderHook(() => useGitSyncProviders())

    expect(result.current).toEqual({ kind: "known", providers: [] })
  })

  it("reports unknown without org settings access", () => {
    mockCanReadOrgSettings = false

    const { result } = renderHook(() => useGitSyncProviders())

    expect(result.current).toEqual({ kind: "unknown" })
  })

  it("reports loading while access is unresolved, disabled, or a check runs", () => {
    mockCanReadOrgSettings = undefined
    expect(renderHook(() => useGitSyncProviders()).result.current).toEqual({
      kind: "loading",
    })

    mockCanReadOrgSettings = true
    expect(
      renderHook(() => useGitSyncProviders({ enabled: false })).result.current
    ).toEqual({ kind: "loading" })

    mockQueries = { ...allNone(), "gitlab-token-credentials-status": loading }
    expect(renderHook(() => useGitSyncProviders()).result.current).toEqual({
      kind: "loading",
    })
  })
})
