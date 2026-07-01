import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { renderHook } from "@testing-library/react"
import type { ReactNode } from "react"
import type { GitHubAppRepository } from "@/client"
import {
  vcsDeleteGithubAppCredentials,
  vcsDeleteGitlabTokenCredentials,
  vcsSaveGithubAppCredentials,
  vcsSaveGitlabTokenCredentials,
} from "@/client"
import {
  useDeleteGitHubAppCredentials,
  useDeleteGitLabTokenCredentials,
  useGitHubAppCredentials,
  useGitLabTokenCredentials,
} from "@/lib/hooks"

jest.mock("@/client", () => {
  const actual = jest.requireActual("@/client")
  return {
    ...actual,
    vcsDeleteGithubAppCredentials: jest.fn(),
    vcsDeleteGitlabTokenCredentials: jest.fn(),
    vcsSaveGithubAppCredentials: jest.fn(),
    vcsSaveGitlabTokenCredentials: jest.fn(),
  }
})

const mockSaveGitHubAppCredentials =
  vcsSaveGithubAppCredentials as jest.MockedFunction<
    typeof vcsSaveGithubAppCredentials
  >

const mockDeleteGitHubAppCredentials =
  vcsDeleteGithubAppCredentials as jest.MockedFunction<
    typeof vcsDeleteGithubAppCredentials
  >
const mockSaveGitLabTokenCredentials =
  vcsSaveGitlabTokenCredentials as jest.MockedFunction<
    typeof vcsSaveGitlabTokenCredentials
  >
const mockDeleteGitLabTokenCredentials =
  vcsDeleteGitlabTokenCredentials as jest.MockedFunction<
    typeof vcsDeleteGitlabTokenCredentials
  >

const cachedRepositories: GitHubAppRepository[] = [
  {
    id: 1,
    name: "repo-a",
    full_name: "test-org/repo-a",
    private: true,
    default_branch: "main",
    git_url: "git+ssh://git@github.com/test-org/repo-a.git",
    html_url: "https://github.com/test-org/repo-a",
    installation_id: 12345678,
    installation_account: "test-org",
    installation_account_type: "Organization",
  },
]

describe("GitHub App credential hooks", () => {
  let queryClient: QueryClient

  beforeEach(() => {
    queryClient = new QueryClient({
      defaultOptions: {
        mutations: { retry: false },
        queries: { retry: false },
      },
    })
    jest.clearAllMocks()
    mockSaveGitHubAppCredentials.mockResolvedValue({
      message: "GitHub App credentials created successfully",
      action: "created",
      app_id: "123456",
    })
    mockDeleteGitHubAppCredentials.mockResolvedValue(undefined)
    mockSaveGitLabTokenCredentials.mockResolvedValue({
      message: "GitLab token credentials created successfully",
      action: "created",
      base_url: "https://gitlab.example.test",
    })
    mockDeleteGitLabTokenCredentials.mockResolvedValue(undefined)
  })

  function wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    )
  }

  it("clears cached repository options when credentials are saved", async () => {
    queryClient.setQueryData(
      ["github-app-repositories", "workspace-1"],
      cachedRepositories
    )

    const { result } = renderHook(() => useGitHubAppCredentials(), { wrapper })

    await result.current.saveCredentials.mutateAsync({
      app_id: "123456",
      private_key: "private-key",
    })

    expect(
      queryClient.getQueryData(["github-app-repositories", "workspace-1"])
    ).toEqual([])
  })

  it("clears cached repository options when credentials are deleted", async () => {
    queryClient.setQueryData(
      ["github-app-repositories", "workspace-1"],
      cachedRepositories
    )

    const { result } = renderHook(() => useDeleteGitHubAppCredentials(), {
      wrapper,
    })

    await result.current.deleteCredentials.mutateAsync()

    expect(
      queryClient.getQueryData(["github-app-repositories", "workspace-1"])
    ).toEqual([])
  })

  it("invalidates GitLab credential-dependent repository queries when credentials are saved", async () => {
    const invalidateQueries = jest.spyOn(queryClient, "invalidateQueries")
    const { result } = renderHook(() => useGitLabTokenCredentials(), {
      wrapper,
    })

    await result.current.saveCredentials.mutateAsync({
      base_url: "https://gitlab.example.test",
      token: "token",
    })

    expect(invalidateQueries).toHaveBeenCalledWith({
      queryKey: ["gitlab-token-credentials-status"],
    })
    expect(invalidateQueries).toHaveBeenCalledWith({
      queryKey: ["workflow-sync-branches"],
    })
    expect(invalidateQueries).toHaveBeenCalledWith({
      queryKey: ["repository_commits"],
    })
  })

  it("invalidates GitLab credential-dependent repository queries when credentials are deleted", async () => {
    const invalidateQueries = jest.spyOn(queryClient, "invalidateQueries")
    const { result } = renderHook(() => useDeleteGitLabTokenCredentials(), {
      wrapper,
    })

    await result.current.deleteCredentials.mutateAsync()

    expect(invalidateQueries).toHaveBeenCalledWith({
      queryKey: ["gitlab-token-credentials-status"],
    })
    expect(invalidateQueries).toHaveBeenCalledWith({
      queryKey: ["workflow-sync-branches"],
    })
    expect(invalidateQueries).toHaveBeenCalledWith({
      queryKey: ["repository_commits"],
    })
  })
})
