/**
 * @jest-environment jsdom
 */

import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type {
  GitBranchInfo,
  GitCommitInfo,
  GitHubAppRepository,
  PullResult,
  VcsProvider,
  WorkspaceRead,
  WorkspaceSyncExportPreview,
} from "@/client"
import { WorkspaceSyncSettings } from "@/components/settings/workspace-sync-settings"
import { Toast, ToastProvider, ToastViewport } from "@/components/ui/toast"
import { toast } from "@/components/ui/use-toast"
import {
  useRepositoryBranches,
  useRepositoryCommits,
  useWorkflowSync,
  useWorkspaceSyncExport,
  useWorkspaceSyncExportPreview,
} from "@/hooks/use-workspace-sync"
import { useGitHubAppRepositories, useWorkspaceSettings } from "@/lib/hooks"

const mockUpdateWorkspace = jest.fn()
const mockExportWorkspace = jest.fn()
const mockPullWorkflows = jest.fn()
const mockRefetchExportPreview = jest.fn()

jest.mock("@/lib/hooks", () => ({
  useGitHubAppRepositories: jest.fn(),
  useWorkspaceSettings: jest.fn(),
}))

jest.mock("@/hooks/use-workspace-sync", () => ({
  useRepositoryBranches: jest.fn(),
  useRepositoryCommits: jest.fn(),
  useWorkflowSync: jest.fn(),
  useWorkspaceSyncExport: jest.fn(),
  useWorkspaceSyncExportPreview: jest.fn(),
}))

jest.mock("@/components/ui/use-toast", () => ({
  toast: jest.fn(),
}))

beforeAll(() => {
  if (!HTMLElement.prototype.hasPointerCapture) {
    Object.defineProperty(HTMLElement.prototype, "hasPointerCapture", {
      value: () => false,
    })
  }
  if (!HTMLElement.prototype.setPointerCapture) {
    Object.defineProperty(HTMLElement.prototype, "setPointerCapture", {
      value: () => undefined,
    })
  }
  if (!HTMLElement.prototype.releasePointerCapture) {
    Object.defineProperty(HTMLElement.prototype, "releasePointerCapture", {
      value: () => undefined,
    })
  }
  if (!HTMLElement.prototype.scrollIntoView) {
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
      value: () => undefined,
    })
  }
})

const repositories: GitHubAppRepository[] = [
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
  {
    id: 2,
    name: "repo-b",
    full_name: "test-org/repo-b",
    private: true,
    default_branch: "trunk",
    git_url: "git+ssh://git@github.com/test-org/repo-b.git",
    html_url: "https://github.com/test-org/repo-b",
    installation_id: 12345678,
    installation_account: "test-org",
    installation_account_type: "Organization",
  },
]

const workspace = {
  id: "workspace-1",
  name: "Workspace 1",
  organization_id: "org-1",
  settings: {
    git_repo_url: null,
    effective_allowed_attachment_extensions: [],
    effective_allowed_attachment_mime_types: [],
  },
} satisfies WorkspaceRead

function setupHooks({
  gitRepoUrl = null,
  gitProvider = null,
  repositoryHook = {},
  branches = [],
  commits = [],
}: {
  gitRepoUrl?: string | null
  gitProvider?: VcsProvider | null
  repositoryHook?: Partial<ReturnType<typeof useGitHubAppRepositories>>
  branches?: GitBranchInfo[]
  commits?: GitCommitInfo[]
} = {}) {
  jest.mocked(useWorkspaceSettings).mockReturnValue({
    updateWorkspace: mockUpdateWorkspace,
    isUpdating: false,
    deleteWorkspace: jest.fn(),
    isDeleting: false,
  } as ReturnType<typeof useWorkspaceSettings>)
  jest.mocked(useGitHubAppRepositories).mockReturnValue({
    repositories,
    repositoriesIsLoading: false,
    repositoriesError: null,
    refetchRepositories: jest.fn(),
    ...repositoryHook,
  } as ReturnType<typeof useGitHubAppRepositories>)
  jest.mocked(useRepositoryBranches).mockReturnValue({
    branches,
    branchesIsLoading: false,
    branchesError: null,
  } as ReturnType<typeof useRepositoryBranches>)
  jest.mocked(useRepositoryCommits).mockReturnValue({
    commits,
    commitsIsLoading: false,
    commitsError: null,
  } as ReturnType<typeof useRepositoryCommits>)
  jest.mocked(useWorkspaceSyncExport).mockReturnValue({
    exportWorkspace: mockExportWorkspace,
    exportWorkspaceIsPending: false,
    exportWorkspaceError: null,
  } as ReturnType<typeof useWorkspaceSyncExport>)
  jest.mocked(useWorkflowSync).mockReturnValue({
    pullWorkflows: mockPullWorkflows,
    pullWorkflowsIsPending: false,
    pullWorkflowsError: null,
  } as ReturnType<typeof useWorkflowSync>)
  jest.mocked(useWorkspaceSyncExportPreview).mockReturnValue({
    preview: undefined,
    previewIsLoading: false,
    previewError: null,
    refetchPreview: mockRefetchExportPreview,
  } as ReturnType<typeof useWorkspaceSyncExportPreview>)

  return {
    ...workspace,
    settings: {
      ...workspace.settings,
      git_provider: gitProvider,
      git_repo_url: gitRepoUrl,
    },
  }
}

describe("WorkspaceSyncSettings", () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockUpdateWorkspace.mockResolvedValue(undefined)
    mockExportWorkspace.mockResolvedValue(undefined)
    mockPullWorkflows.mockResolvedValue(undefined)
  })

  it("allows manual git URLs when app repositories are available", async () => {
    const user = userEvent.setup()
    render(<WorkspaceSyncSettings workspace={setupHooks()} />)

    await user.click(screen.getByRole("button", { name: "Manual" }))
    const customUrl =
      "git+ssh://git@github.com/test-org/custom-repo.git@feature/review-fix"
    await user.type(screen.getByLabelText("Remote repository URL"), customUrl)
    await user.click(screen.getByRole("button", { name: "Save" }))

    await waitFor(() => {
      expect(mockUpdateWorkspace).toHaveBeenCalledWith({
        settings: {
          git_provider: "github",
          git_repo_url: customUrl,
        },
      })
    })
  })

  it("saves a GitLab manual URL and suppresses the GitHub repository picker", async () => {
    const user = userEvent.setup()
    render(
      <WorkspaceSyncSettings
        workspace={setupHooks({ gitProvider: "gitlab" })}
      />
    )

    expect(useGitHubAppRepositories).toHaveBeenCalledWith("workspace-1", {
      enabled: false,
    })
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument()
    expect(screen.getByLabelText("Remote repository URL")).toHaveAttribute(
      "placeholder",
      "git+ssh://git@gitlab.com/my-org/my-group/my-repo.git"
    )

    const gitlabUrl =
      "git+ssh://git@gitlab.com/test-org/subgroup/custom-repo.git"
    await user.type(screen.getByLabelText("Remote repository URL"), gitlabUrl)
    await user.click(screen.getByRole("button", { name: "Save" }))

    await waitFor(() => {
      expect(mockUpdateWorkspace).toHaveBeenCalledWith({
        settings: {
          git_provider: "gitlab",
          git_repo_url: gitlabUrl,
        },
      })
    })
  })

  it("requires an explicit supported provider choice for unsupported persisted providers", async () => {
    const user = userEvent.setup()
    render(
      <WorkspaceSyncSettings
        workspace={setupHooks({ gitProvider: "bitbucket" })}
      />
    )

    expect(
      screen.getByText(/The saved provider "bitbucket" is not supported/)
    ).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled()

    await user.click(screen.getByRole("button", { name: "GitLab" }))
    const gitlabUrl =
      "git+ssh://git@gitlab.com/test-org/subgroup/custom-repo.git"
    await user.type(screen.getByLabelText("Remote repository URL"), gitlabUrl)
    await user.click(screen.getByRole("button", { name: "Save" }))

    await waitFor(() => {
      expect(mockUpdateWorkspace).toHaveBeenCalledWith({
        settings: {
          git_provider: "gitlab",
          git_repo_url: gitlabUrl,
        },
      })
    })
  })

  it("selects an app repository when repository options are available", async () => {
    const user = userEvent.setup()
    render(<WorkspaceSyncSettings workspace={setupHooks()} />)

    await user.click(screen.getByRole("combobox"))
    await user.click(
      await screen.findByRole("option", { name: "test-org/repo-a" })
    )
    await user.click(screen.getByRole("button", { name: "Save" }))

    await waitFor(() => {
      expect(mockUpdateWorkspace).toHaveBeenCalledWith({
        settings: {
          git_provider: "github",
          git_repo_url: repositories[0].git_url,
        },
      })
    })
  })

  it("preserves a selected app repository's non-main default branch", async () => {
    const user = userEvent.setup()
    render(<WorkspaceSyncSettings workspace={setupHooks()} />)

    await user.click(screen.getByRole("combobox"))
    await user.click(
      await screen.findByRole("option", { name: "test-org/repo-b" })
    )
    await user.click(screen.getByRole("button", { name: "Save" }))

    await waitFor(() => {
      expect(mockUpdateWorkspace).toHaveBeenCalledWith({
        settings: {
          git_provider: "github",
          git_repo_url: `${repositories[1].git_url}@trunk`,
        },
      })
    })
  })

  it("opens in manual mode for an existing custom git URL", async () => {
    const user = userEvent.setup()
    const customUrl =
      "git+ssh://git@github.com/test-org/custom-repo.git@feature/custom"

    render(
      <WorkspaceSyncSettings
        workspace={setupHooks({ gitRepoUrl: customUrl })}
      />
    )

    await user.click(screen.getByRole("button", { name: "Edit connection" }))
    await waitFor(() => {
      expect(screen.getByDisplayValue(customUrl)).toBeInTheDocument()
    })
    expect(screen.getByRole("button", { name: "Manual" })).toHaveAttribute(
      "aria-current",
      "true"
    )
  })

  it("keeps explicit select mode for an existing custom git URL", async () => {
    const user = userEvent.setup()
    const customUrl =
      "git+ssh://git@github.com/test-org/custom-repo.git@feature/custom"
    const { rerender } = render(
      <WorkspaceSyncSettings
        workspace={setupHooks({ gitRepoUrl: customUrl })}
      />
    )

    await user.click(screen.getByRole("button", { name: "Edit connection" }))
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Manual" })).toHaveAttribute(
        "aria-current",
        "true"
      )
    })

    await user.click(screen.getByRole("button", { name: "Select" }))
    expect(screen.getByRole("button", { name: "Select" })).toHaveAttribute(
      "aria-current",
      "true"
    )

    rerender(
      <WorkspaceSyncSettings
        workspace={setupHooks({ gitRepoUrl: customUrl })}
      />
    )

    expect(screen.getByRole("button", { name: "Select" })).toHaveAttribute(
      "aria-current",
      "true"
    )
    expect(screen.getByRole("combobox")).toBeInTheDocument()
    expect(
      screen.queryByRole("textbox", { name: "Remote repository URL" })
    ).not.toBeInTheDocument()
  })

  it("falls back to manual entry when repositories cannot load", () => {
    render(
      <WorkspaceSyncSettings
        workspace={setupHooks({
          repositoryHook: {
            repositories: [],
            repositoriesError: new Error("Failed to load repositories"),
          },
        })}
      />
    )

    expect(screen.getByLabelText("Remote repository URL")).toBeInTheDocument()
    expect(
      screen.getByText(
        "Could not load GitHub App repositories. Enter a git+ssh URL manually."
      )
    ).toBeInTheDocument()
  })

  it("keeps cached app repositories when a repository refetch errors", () => {
    render(
      <WorkspaceSyncSettings
        workspace={setupHooks({
          repositoryHook: {
            repositories,
            repositoriesError: new Error("Failed to load repositories"),
          },
        })}
      />
    )

    expect(screen.getByRole("combobox")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Select" })).toHaveAttribute(
      "aria-current",
      "true"
    )
  })

  it("passes the persisted GitLab provider into repository health checks", () => {
    const gitlabUrl =
      "git+ssh://git@gitlab.com/test-org/subgroup/custom-repo.git"
    render(
      <WorkspaceSyncSettings
        workspace={setupHooks({
          gitRepoUrl: gitlabUrl,
          gitProvider: "gitlab",
          branches: [{ name: "main", is_default: true }],
        })}
      />
    )

    expect(useGitHubAppRepositories).toHaveBeenCalledWith("workspace-1", {
      enabled: false,
    })
    expect(useRepositoryBranches).toHaveBeenCalledWith(
      "workspace-1",
      expect.objectContaining({
        gitRepoUrl: gitlabUrl,
        provider: "gitlab",
      })
    )
    expect(useRepositoryCommits).toHaveBeenCalledWith(
      "workspace-1",
      expect.objectContaining({
        gitRepoUrl: gitlabUrl,
        provider: "gitlab",
      })
    )
  })

  it("shows the push resource preview for connected workspaces", async () => {
    const user = userEvent.setup()
    const preview: WorkspaceSyncExportPreview = {
      resource_counts: {
        workflow: 2,
        agent_preset: 0,
        skill: 0,
        table: 1,
        case_tag: 1,
        case_field: 0,
        case_dropdown: 0,
        case_duration: 0,
        variable: 1,
        secret_metadata: 0,
      },
      files: [
        "workflows/root/definition.yml",
        "workflows/child/definition.yml",
        "tables/indicators/table.yml",
        "case_tags/escalated.yml",
        "variables/default/escalation.yml",
      ],
      resources: [
        {
          resource_type: "workflow",
          source_id: "root",
          name: "Root workflow",
          path: "workflows/root/definition.yml",
        },
        {
          resource_type: "workflow",
          source_id: "child",
          name: "Child workflow",
          path: "workflows/child/definition.yml",
        },
        {
          resource_type: "table",
          source_id: "indicators",
          name: "Indicators",
          path: "tables/indicators/table.yml",
        },
        {
          resource_type: "case_tag",
          source_id: "escalated",
          name: "Escalated",
          path: "case_tags/escalated.yml",
        },
        {
          resource_type: "variable",
          source_id: "default/escalation",
          name: "Escalation",
          path: "variables/default/escalation.yml",
        },
      ],
      resource_diffs: [
        {
          resource_type: "workflow",
          source_id: "root",
          source_path: "workflows/root/definition.yml",
          change_type: "modified",
          title: "Root workflow",
          diff: "@@ -1 +1 @@\n-old\n+new",
        },
      ],
    }
    const connectedWorkspace = setupHooks({
      gitRepoUrl: repositories[0].git_url,
      branches: [{ name: "main", is_default: true }],
    })
    jest.mocked(useWorkspaceSyncExportPreview).mockReturnValue({
      preview,
      previewIsLoading: false,
      previewError: null,
      refetchPreview: mockRefetchExportPreview,
    } as ReturnType<typeof useWorkspaceSyncExportPreview>)

    render(<WorkspaceSyncSettings workspace={connectedWorkspace} />)

    expect(screen.getByText("Preview")).toBeInTheDocument()
    expect(screen.getByText("changes against main")).toBeInTheDocument()
    expect(screen.queryByText("Included in this push")).not.toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: "Preview changes" }))

    expect(mockRefetchExportPreview).toHaveBeenCalledTimes(1)
    expect(screen.getByText("Included in this push")).toBeInTheDocument()
    expect(screen.getByText("5 files")).toBeInTheDocument()
    expect(screen.getAllByText("Workflows").length).toBeGreaterThan(0)
    expect(
      screen.getByText("Root workflow, Child workflow")
    ).toBeInTheDocument()
    expect(screen.getByText("Case tags")).toBeInTheDocument()
    expect(screen.getByText("Variables")).toBeInTheDocument()
    expect(screen.getByLabelText("Modified")).toBeInTheDocument()
    expect(
      screen.getByText("workflows/root/definition.yml")
    ).toBeInTheDocument()
  })

  it("renders the workspace push review request as an external link", async () => {
    const user = userEvent.setup()
    const prUrl = "https://github.com/test-org/repo-a/pull/42"
    const connectedWorkspace = setupHooks({
      gitRepoUrl: repositories[0].git_url,
      branches: [{ name: "main", is_default: true }],
    })
    mockExportWorkspace.mockResolvedValue({
      commit: {
        status: "committed",
        sha: "a".repeat(40),
        ref: "sync/workspace-test",
        base_ref: "main",
        pr_url: prUrl,
        message: "Export workspace config",
      },
      files: ["tracecat.json"],
    })

    render(<WorkspaceSyncSettings workspace={connectedWorkspace} />)

    await user.click(screen.getByRole("button", { name: "Push & open PR" }))

    await waitFor(() => {
      expect(toast).toHaveBeenCalledWith(
        expect.objectContaining({
          title: "Pull request ready",
          description: "Export workspace config",
          action: expect.anything(),
        })
      )
    })

    const toastOptions = jest.mocked(toast).mock.calls[0][0]
    render(
      <ToastProvider>
        <Toast open>{toastOptions.action}</Toast>
        <ToastViewport />
      </ToastProvider>
    )

    expect(screen.getByRole("link", { name: "View PR" })).toHaveAttribute(
      "href",
      prUrl
    )
    expect(screen.getByRole("link", { name: "View PR" })).toHaveAttribute(
      "target",
      "_blank"
    )
    expect(screen.getByRole("link", { name: "View PR" })).toHaveAttribute(
      "rel",
      "noopener noreferrer"
    )
  })

  it("keeps pull actions available after previewing changes", async () => {
    const user = userEvent.setup()
    const commitSha = "a".repeat(40)
    const preview: PullResult = {
      success: true,
      commit_sha: commitSha,
      workflows_found: 1,
      workflows_imported: 0,
      diagnostics: [],
      message: "Dry run completed - 1 resource change(s) detected",
      resource_counts: {
        workflow: { found: 1, imported: 0 },
        table: { found: 1, imported: 0 },
      },
      files: [
        "tracecat.json",
        "workflows/root/definition.yml",
        "tables/indicators/table.yml",
      ],
      resources: [
        {
          resource_type: "workflow",
          source_id: "root",
          name: "Root workflow",
          path: "workflows/root/definition.yml",
        },
        {
          resource_type: "table",
          source_id: "indicators",
          name: "Indicators",
          path: "tables/indicators/table.yml",
        },
      ],
      resource_diffs: [
        {
          resource_type: "workflow",
          source_id: "root",
          source_path: "workflows/root/definition.yml",
          change_type: "modified",
          title: "Root workflow",
          diff: "@@ -1 +1 @@\n-old\n+new",
        },
      ],
    }
    const connectedWorkspace = setupHooks({
      gitRepoUrl: repositories[0].git_url,
      branches: [{ name: "main", is_default: true }],
      commits: [
        {
          sha: commitSha,
          message: "Update workspace resources",
          author: "Test Author",
          author_email: "author@example.com",
          date: "2026-06-24T12:00:00Z",
        },
      ],
    })
    mockPullWorkflows.mockResolvedValue(preview)

    const { container } = render(
      <WorkspaceSyncSettings workspace={connectedWorkspace} />
    )

    await user.click(screen.getByRole("tab", { name: "Pull" }))
    await user.click(screen.getByRole("button", { name: "Preview changes" }))

    await waitFor(() => {
      expect(mockPullWorkflows).toHaveBeenCalledWith({
        commit_sha: commitSha,
        dry_run: true,
        sync_schedules: false,
      })
    })
    expect(screen.getByText("Included in this pull")).toBeInTheDocument()
    expect(screen.getByText("3 files")).toBeInTheDocument()
    expect(screen.getAllByText("Root workflow").length).toBeGreaterThan(0)
    expect(screen.getByText("Indicators")).toBeInTheDocument()
    expect(screen.getByLabelText("Modified")).toBeInTheDocument()
    expect(container.firstElementChild).toHaveClass("min-w-0")

    const applyPullButton = screen.getByRole("button", { name: "Apply pull" })
    expect(applyPullButton).toBeEnabled()

    await user.click(applyPullButton)

    await waitFor(() => {
      expect(mockPullWorkflows).toHaveBeenLastCalledWith({
        commit_sha: commitSha,
        sync_schedules: false,
      })
    })
  })

  it("disables the repository selector while repositories are loading", () => {
    render(
      <WorkspaceSyncSettings
        workspace={setupHooks({
          repositoryHook: {
            repositories: [],
            repositoriesIsLoading: true,
          },
        })}
      />
    )

    expect(screen.getByRole("combobox")).toBeDisabled()
    expect(screen.getByText("Loading repositories...")).toBeInTheDocument()
  })
})
