import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { renderHook, waitFor } from "@testing-library/react"
import type { ReactNode } from "react"
import {
  workflowsListWorkflowBranches,
  workflowsListWorkflowCommits,
} from "@/client"
import {
  useRepositoryBranches,
  useRepositoryCommits,
} from "@/hooks/use-workspace-sync"

jest.mock("@/client", () => {
  const actual = jest.requireActual("@/client")
  return {
    ...actual,
    workflowsExportWorkspaceSync: jest.fn(),
    workflowsListWorkflowBranches: jest.fn(),
    workflowsListWorkflowCommits: jest.fn(),
    workflowsPreviewExportWorkspaceSync: jest.fn(),
    workflowsPullWorkflows: jest.fn(),
  }
})

const mockWorkflowsListWorkflowBranches =
  workflowsListWorkflowBranches as jest.MockedFunction<
    typeof workflowsListWorkflowBranches
  >
const mockWorkflowsListWorkflowCommits =
  workflowsListWorkflowCommits as jest.MockedFunction<
    typeof workflowsListWorkflowCommits
  >

function createWrapper(queryClient: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    )
  }
}

describe("workspace sync repository queries", () => {
  let queryClient: QueryClient

  beforeEach(() => {
    queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
      },
    })
    mockWorkflowsListWorkflowBranches.mockResolvedValue([])
    mockWorkflowsListWorkflowCommits.mockResolvedValue([])
  })

  afterEach(() => {
    jest.clearAllMocks()
  })

  it("keys branch queries by configured git URL", () => {
    const wrapper = createWrapper(queryClient)

    renderHook(
      () =>
        useRepositoryBranches("workspace-1", {
          enabled: false,
          gitRepoUrl: "git+ssh://git@github.com/test/repo-a.git",
        }),
      { wrapper }
    )
    renderHook(
      () =>
        useRepositoryBranches("workspace-1", {
          enabled: false,
          gitRepoUrl: "git+ssh://git@github.com/test/repo-b.git",
        }),
      { wrapper }
    )

    expect(
      queryClient.getQueryCache().find({
        queryKey: [
          "workflow-sync-branches",
          "workspace-1",
          "git+ssh://git@github.com/test/repo-a.git",
          "github",
          200,
        ],
      })
    ).toBeDefined()
    expect(
      queryClient.getQueryCache().find({
        queryKey: [
          "workflow-sync-branches",
          "workspace-1",
          "git+ssh://git@github.com/test/repo-b.git",
          "github",
          200,
        ],
      })
    ).toBeDefined()
  })

  it("keys branch queries by provider", () => {
    const wrapper = createWrapper(queryClient)

    renderHook(
      () =>
        useRepositoryBranches("workspace-1", {
          enabled: false,
          gitRepoUrl: "git+ssh://git@example.com/test/repo.git",
          provider: "github",
        }),
      { wrapper }
    )
    renderHook(
      () =>
        useRepositoryBranches("workspace-1", {
          enabled: false,
          gitRepoUrl: "git+ssh://git@example.com/test/repo.git",
          provider: "gitlab",
        }),
      { wrapper }
    )

    expect(
      queryClient.getQueryCache().find({
        queryKey: [
          "workflow-sync-branches",
          "workspace-1",
          "git+ssh://git@example.com/test/repo.git",
          "github",
          200,
        ],
      })
    ).toBeDefined()
    expect(
      queryClient.getQueryCache().find({
        queryKey: [
          "workflow-sync-branches",
          "workspace-1",
          "git+ssh://git@example.com/test/repo.git",
          "gitlab",
          200,
        ],
      })
    ).toBeDefined()
  })

  it("keys commit queries by configured git URL", () => {
    const wrapper = createWrapper(queryClient)

    renderHook(
      () =>
        useRepositoryCommits("workspace-1", {
          branch: "main",
          enabled: false,
          gitRepoUrl: "git+ssh://git@github.com/test/repo-a.git",
        }),
      { wrapper }
    )
    renderHook(
      () =>
        useRepositoryCommits("workspace-1", {
          branch: "main",
          enabled: false,
          gitRepoUrl: "git+ssh://git@github.com/test/repo-b.git",
        }),
      { wrapper }
    )

    expect(
      queryClient.getQueryCache().find({
        queryKey: [
          "repository_commits",
          "workspace-1",
          "git+ssh://git@github.com/test/repo-a.git",
          "github",
          "main",
          10,
        ],
      })
    ).toBeDefined()
    expect(
      queryClient.getQueryCache().find({
        queryKey: [
          "repository_commits",
          "workspace-1",
          "git+ssh://git@github.com/test/repo-b.git",
          "github",
          "main",
          10,
        ],
      })
    ).toBeDefined()
  })

  it("omits provider from branch requests (server derives it)", async () => {
    const wrapper = createWrapper(queryClient)

    renderHook(
      () =>
        useRepositoryBranches("workspace-1", {
          gitRepoUrl: "git+ssh://git@gitlab.com/test/repo.git",
          provider: "gitlab",
          limit: 50,
        }),
      { wrapper }
    )

    await waitFor(() => {
      expect(mockWorkflowsListWorkflowBranches).toHaveBeenCalledWith({
        limit: 50,
        workspaceId: "workspace-1",
      })
    })
  })

  it("omits provider from commit requests (server derives it)", async () => {
    const wrapper = createWrapper(queryClient)

    renderHook(
      () =>
        useRepositoryCommits("workspace-1", {
          branch: "release",
          gitRepoUrl: "git+ssh://git@gitlab.com/test/repo.git",
          provider: "gitlab",
          limit: 25,
        }),
      { wrapper }
    )

    await waitFor(() => {
      expect(mockWorkflowsListWorkflowCommits).toHaveBeenCalledWith({
        branch: "release",
        limit: 25,
        workspaceId: "workspace-1",
      })
    })
  })
})
