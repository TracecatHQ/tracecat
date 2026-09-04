/**
 * @jest-environment jsdom
 */

import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type {
  CatalogMappingRequirement,
  GitBranchInfo,
  GitCommitInfo,
  GitHubAppRepository,
  McpIntegrationMappingRequirement,
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

function createCatalogMappingRequirement(
  sourceCatalogId: string,
  targetCatalogId: string
): CatalogMappingRequirement {
  return {
    source_catalog_id: sourceCatalogId,
    model_provider: "custom-model-provider",
    model_name: "shared-model",
    reason: "ambiguous",
    message: "Choose the target model before applying this pull.",
    candidates: [
      {
        catalog_id: targetCatalogId,
        model_provider: "custom-model-provider",
        model_name: "shared-model",
        provider_name: "Provider East",
        model_display_name: null,
        endpoint_hostname: "east.models.example.com",
        origin: "custom_provider",
      },
    ],
    affected_presets: [],
    affected_workflows: [],
  }
}

function createMcpMappingRequirement(
  sourceMcpIntegrationId: string,
  targetMcpIntegrationId: string
): McpIntegrationMappingRequirement {
  return {
    source_mcp_integration_id: sourceMcpIntegrationId,
    slug: "shared-mcp",
    name: "Shared MCP",
    server_type: "http",
    auth_type: "oauth",
    reason: "unresolved",
    message: "Choose the target MCP integration before applying this pull.",
    candidates: [
      {
        mcp_integration_id: targetMcpIntegrationId,
        slug: "shared-mcp",
        name: "Shared MCP",
        server_type: "http",
        auth_type: "oauth",
      },
    ],
    affected_presets: [],
    affected_workflows: [],
    affected_skills: [],
  }
}

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
    const resourceDiffs = Array.from({ length: 25 }, (_, index) => ({
      resource_type: "workflow" as const,
      source_id: `workflow-${index}`,
      source_path: `workflows/workflow-${index}/definition.yml`,
      change_type: "modified" as const,
      title: `Workflow ${index}`,
      diff: "@@ -1 +1 @@\n-old\n+new",
    }))
    const preview: PullResult = {
      success: true,
      commit_sha: commitSha,
      workflows_found: resourceDiffs.length,
      workflows_imported: 0,
      diagnostics: [],
      message: `Dry run completed - ${resourceDiffs.length} resource change(s) detected`,
      resource_counts: {
        workflow: { found: resourceDiffs.length, imported: 0 },
        table: { found: 1, imported: 0 },
      },
      files: [
        "tracecat.json",
        "tables/indicators/table.yml",
        ...resourceDiffs.map((diff) => diff.source_path),
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
      resource_diffs: resourceDiffs,
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
        catalog_mappings: [],
        mcp_integration_mappings: [],
      })
    })
    expect(screen.getByText("Included in this pull")).toBeInTheDocument()
    expect(
      screen.getByText(`${resourceDiffs.length + 2} files`)
    ).toBeInTheDocument()
    expect(screen.getAllByText("Root workflow").length).toBeGreaterThan(0)
    expect(screen.getByText("Indicators")).toBeInTheDocument()
    expect(screen.getAllByLabelText("Modified")).toHaveLength(
      resourceDiffs.length
    )
    expect(container.firstElementChild).toHaveClass("min-w-0")

    expect(screen.getByRole("group", { name: "Pull actions" })).toHaveClass(
      "sticky",
      "bottom-0",
      "z-10",
      "bg-background",
      "after:h-8",
      "after:bg-background"
    )
    const applyPullButton = screen.getByRole("button", { name: "Apply pull" })
    expect(applyPullButton).toBeEnabled()

    await user.click(applyPullButton)

    await waitFor(() => {
      expect(mockPullWorkflows).toHaveBeenLastCalledWith({
        commit_sha: commitSha,
        sync_schedules: false,
        catalog_mappings: [],
        mcp_integration_mappings: [],
      })
    })
  })

  it("requires an ambiguous target model choice and re-previews it", async () => {
    const user = userEvent.setup()
    const commitSha = "b".repeat(40)
    const sourceCatalogId = "11111111-1111-1111-1111-111111111111"
    const targetCatalogId = "22222222-2222-2222-2222-222222222222"
    const replacementCatalogId = "33333333-3333-3333-3333-333333333333"
    const ambiguousPreview: PullResult = {
      success: false,
      commit_sha: commitSha,
      workflows_found: 0,
      workflows_imported: 0,
      diagnostics: [
        {
          workflow_path: "agent_presets/triage/versions/1.yml",
          workflow_title: "Triage",
          error_type: "dependency",
          message: "Choose the target model before applying this pull.",
          details: { code: "catalog_mapping_required" },
        },
      ],
      message: "Import failed: 1 validation error(s) found",
      resource_diffs: [],
      catalog_mapping_requirements: [
        {
          source_catalog_id: sourceCatalogId,
          model_provider: "custom-model-provider",
          model_name: "shared-model",
          reason: "ambiguous",
          message: "Choose the target model before applying this pull.",
          candidates: [
            {
              catalog_id: targetCatalogId,
              model_provider: "custom-model-provider",
              model_name: "shared-model",
              provider_name: "Provider East",
              model_display_name: null,
              endpoint_hostname: "east.models.example.com",
              origin: "custom_provider",
            },
            {
              catalog_id: replacementCatalogId,
              model_provider: "custom-model-provider",
              model_name: "shared-model",
              provider_name: "Provider West",
              model_display_name: null,
              endpoint_hostname: "west.models.example.com",
              origin: "custom_provider",
            },
          ],
          affected_presets: [
            {
              preset_slug: "triage",
              preset_name: "Triage",
              version: 1,
              path: "agent_presets/triage/versions/1.yml",
            },
            {
              preset_slug: "investigate",
              preset_name: "Investigate",
              version: 2,
              path: "agent_presets/investigate/versions/2.yml",
            },
          ],
          affected_workflows: [
            {
              workflow_source_id: "triage-alert",
              workflow_path: "workflows/triage-alert/definition.yml",
              workflow_title: "Triage alert",
              action_ref: "run_triage_agent",
            },
          ],
        },
      ],
    }
    const resolvedPreview: PullResult = {
      ...ambiguousPreview,
      success: true,
      diagnostics: [],
      message: "Dry run completed - 1 resource change(s) detected",
      catalog_mapping_requirements: [],
    }
    const appliedResult: PullResult = {
      ...resolvedPreview,
      workflows_imported: 0,
      message: "Successfully imported workspace resources",
    }
    const connectedWorkspace = setupHooks({
      gitRepoUrl: repositories[0].git_url,
      branches: [{ name: "main", is_default: true }],
      commits: [
        {
          sha: commitSha,
          message: "Import shared model",
          author: "Test Author",
          author_email: "author@example.com",
          date: "2026-07-24T12:00:00Z",
        },
      ],
    })
    mockPullWorkflows
      .mockResolvedValueOnce(ambiguousPreview)
      .mockResolvedValueOnce(resolvedPreview)
      .mockResolvedValueOnce(appliedResult)

    render(<WorkspaceSyncSettings workspace={connectedWorkspace} />)

    await user.click(screen.getByRole("tab", { name: "Pull" }))
    await user.click(screen.getByRole("button", { name: "Preview changes" }))

    const applyPullButton = screen.getByRole("button", { name: "Apply pull" })
    expect(applyPullButton).toBeDisabled()
    expect(screen.getByText("Choose target models")).toBeInTheDocument()
    expect(
      screen.getByText(
        /Triage version 1, Investigate version 2, Triage alert action run_triage_agent/
      )
    ).toBeInTheDocument()

    await user.click(screen.getByLabelText("Target model for shared-model"))
    await user.click(
      screen.getByRole("option", {
        name: "Provider East · east.models.example.com",
      })
    )

    expect(
      screen.getByText(
        "Preview changes again to validate these choices before applying."
      )
    ).toBeInTheDocument()
    expect(applyPullButton).toBeDisabled()

    await user.click(screen.getByRole("button", { name: "Preview changes" }))
    await waitFor(() => {
      expect(mockPullWorkflows).toHaveBeenNthCalledWith(2, {
        commit_sha: commitSha,
        dry_run: true,
        sync_schedules: false,
        catalog_mappings: [
          {
            source_catalog_id: sourceCatalogId,
            target_catalog_id: targetCatalogId,
          },
        ],
        mcp_integration_mappings: [],
      })
    })
    expect(applyPullButton).toBeEnabled()
    expect(
      screen.queryByLabelText("Target model for shared-model")
    ).not.toBeInTheDocument()

    await user.click(applyPullButton)
    await waitFor(() => {
      expect(mockPullWorkflows).toHaveBeenNthCalledWith(3, {
        commit_sha: commitSha,
        sync_schedules: false,
        catalog_mappings: [
          {
            source_catalog_id: sourceCatalogId,
            target_catalog_id: targetCatalogId,
          },
        ],
        mcp_integration_mappings: [],
      })
    })
  })

  it("clears obsolete model choices when a later preview has no candidates", async () => {
    const user = userEvent.setup()
    const commitSha = "c".repeat(40)
    const sourceCatalogId = "11111111-1111-1111-1111-111111111111"
    const targetCatalogId = "22222222-2222-2222-2222-222222222222"
    const requirement = createCatalogMappingRequirement(
      sourceCatalogId,
      targetCatalogId
    )
    const ambiguousPreview: PullResult = {
      success: false,
      commit_sha: commitSha,
      workflows_found: 0,
      workflows_imported: 0,
      diagnostics: [],
      message: "Choose the target model before applying this pull.",
      resource_diffs: [],
      catalog_mapping_requirements: [requirement],
    }
    const unavailablePreview: PullResult = {
      ...ambiguousPreview,
      message: "No matching catalogs are available.",
      catalog_mapping_requirements: [],
    }
    const connectedWorkspace = setupHooks({
      gitRepoUrl: repositories[0].git_url,
      branches: [{ name: "main", is_default: true }],
      commits: [
        {
          sha: commitSha,
          message: "Import shared model",
          author: "Test Author",
          author_email: "author@example.com",
          date: "2026-07-24T12:00:00Z",
        },
      ],
    })
    mockPullWorkflows
      .mockResolvedValueOnce(ambiguousPreview)
      .mockResolvedValueOnce(unavailablePreview)

    render(<WorkspaceSyncSettings workspace={connectedWorkspace} />)

    await user.click(screen.getByRole("tab", { name: "Pull" }))
    await user.click(screen.getByRole("button", { name: "Preview changes" }))
    expect(
      screen.getByLabelText("Target model for shared-model")
    ).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: "Preview changes" }))

    await waitFor(() => {
      expect(
        screen.queryByLabelText("Target model for shared-model")
      ).not.toBeInTheDocument()
    })
  })

  it("clears obsolete model choices when an apply failure omits requirements", async () => {
    const user = userEvent.setup()
    const commitSha = "d".repeat(40)
    const sourceCatalogId = "11111111-1111-1111-1111-111111111111"
    const targetCatalogId = "22222222-2222-2222-2222-222222222222"
    const requirement = createCatalogMappingRequirement(
      sourceCatalogId,
      targetCatalogId
    )
    const successfulPreview: PullResult = {
      success: true,
      commit_sha: commitSha,
      workflows_found: 0,
      workflows_imported: 0,
      diagnostics: [],
      message: "Dry run completed",
      resource_diffs: [],
      catalog_mapping_requirements: [requirement],
    }
    const failedApply: PullResult = {
      success: false,
      commit_sha: commitSha,
      workflows_found: 0,
      workflows_imported: 0,
      diagnostics: [],
      message: "No matching catalogs are available.",
      resource_diffs: [],
    }
    const connectedWorkspace = setupHooks({
      gitRepoUrl: repositories[0].git_url,
      branches: [{ name: "main", is_default: true }],
      commits: [
        {
          sha: commitSha,
          message: "Import shared model",
          author: "Test Author",
          author_email: "author@example.com",
          date: "2026-07-24T12:00:00Z",
        },
      ],
    })
    mockPullWorkflows
      .mockResolvedValueOnce(successfulPreview)
      .mockResolvedValueOnce(failedApply)

    render(<WorkspaceSyncSettings workspace={connectedWorkspace} />)

    await user.click(screen.getByRole("tab", { name: "Pull" }))
    await user.click(screen.getByRole("button", { name: "Preview changes" }))
    expect(screen.getByRole("button", { name: "Apply pull" })).toBeEnabled()
    expect(
      screen.getByLabelText("Target model for shared-model")
    ).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: "Apply pull" }))

    await waitFor(() => {
      expect(
        screen.queryByLabelText("Target model for shared-model")
      ).not.toBeInTheDocument()
    })
  })

  it("requires an unresolved MCP integration choice and re-previews it", async () => {
    const user = userEvent.setup()
    const commitSha = "e".repeat(40)
    const sourceMcpId = "44444444-4444-4444-4444-444444444444"
    const targetMcpId = "55555555-5555-5555-5555-555555555555"
    const replacementMcpId = "66666666-6666-6666-6666-666666666666"
    const unresolvedPreview: PullResult = {
      success: false,
      commit_sha: commitSha,
      workflows_found: 0,
      workflows_imported: 0,
      diagnostics: [
        {
          workflow_path: "agent_presets/triage/versions/1.yml",
          workflow_title: "Triage",
          error_type: "dependency",
          message:
            "Choose the target MCP integration before applying this pull.",
          details: { code: "mcp_integration_mapping_required" },
        },
      ],
      message: "Import failed: 1 validation error(s) found",
      resource_diffs: [],
      mcp_integration_mapping_requirements: [
        {
          source_mcp_integration_id: sourceMcpId,
          slug: "shared-mcp",
          name: "Shared MCP",
          server_type: "http",
          auth_type: "oauth",
          reason: "unresolved",
          message:
            "Choose the target MCP integration before applying this pull.",
          candidates: [
            {
              mcp_integration_id: targetMcpId,
              slug: "shared-mcp-east",
              name: "Shared MCP East",
              server_type: "http",
              auth_type: "oauth",
            },
            {
              mcp_integration_id: replacementMcpId,
              slug: "shared-mcp-west",
              name: "Shared MCP West",
              server_type: "sse",
              auth_type: "api_key",
            },
          ],
          affected_presets: [
            {
              preset_slug: "triage",
              preset_name: "Triage",
              version: 1,
              path: "agent_presets/triage/versions/1.yml",
            },
          ],
          affected_workflows: [
            {
              workflow_source_id: "triage-alert",
              workflow_path: "workflows/triage-alert/definition.yml",
              workflow_title: "Triage alert",
              action_ref: "run_triage_agent",
            },
          ],
          affected_skills: [
            {
              skill_source_id: "incident-triage",
              skill_name: "Incident triage",
              path: "skills/incident-triage/skill.yml",
              tool_ids: ["mcp.shared-mcp.find_issue"],
            },
          ],
        },
      ],
    }
    const resolvedPreview: PullResult = {
      ...unresolvedPreview,
      success: true,
      diagnostics: [],
      message: "Dry run completed - 1 resource change(s) detected",
      mcp_integration_mapping_requirements: [],
    }
    const appliedResult: PullResult = {
      ...resolvedPreview,
      message: "Successfully imported workspace resources",
    }
    const connectedWorkspace = setupHooks({
      gitRepoUrl: repositories[0].git_url,
      branches: [{ name: "main", is_default: true }],
      commits: [
        {
          sha: commitSha,
          message: "Import shared MCP integration",
          author: "Test Author",
          author_email: "author@example.com",
          date: "2026-08-11T12:00:00Z",
        },
      ],
    })
    mockPullWorkflows
      .mockResolvedValueOnce(unresolvedPreview)
      .mockResolvedValueOnce(resolvedPreview)
      .mockResolvedValueOnce(appliedResult)

    render(<WorkspaceSyncSettings workspace={connectedWorkspace} />)

    await user.click(screen.getByRole("tab", { name: "Pull" }))
    await user.click(screen.getByRole("button", { name: "Preview changes" }))

    const applyPullButton = screen.getByRole("button", { name: "Apply pull" })
    expect(applyPullButton).toBeDisabled()
    expect(
      screen.getByText("Choose target MCP integrations")
    ).toBeInTheDocument()
    expect(
      screen.getByText(
        /Triage version 1, Triage alert action run_triage_agent, Incident triage/
      )
    ).toBeInTheDocument()

    await user.click(
      screen.getByLabelText("Target MCP integration for shared-mcp")
    )
    await user.click(
      screen.getByRole("option", { name: "Shared MCP East (http · oauth)" })
    )

    expect(
      screen.getByText(
        "Preview changes again to validate these choices before applying."
      )
    ).toBeInTheDocument()
    expect(applyPullButton).toBeDisabled()

    await user.click(screen.getByRole("button", { name: "Preview changes" }))
    await waitFor(() => {
      expect(mockPullWorkflows).toHaveBeenNthCalledWith(2, {
        commit_sha: commitSha,
        dry_run: true,
        sync_schedules: false,
        catalog_mappings: [],
        mcp_integration_mappings: [
          {
            source_mcp_integration_id: sourceMcpId,
            target_mcp_integration_id: targetMcpId,
          },
        ],
      })
    })
    expect(applyPullButton).toBeEnabled()
    expect(
      screen.queryByLabelText("Target MCP integration for shared-mcp")
    ).not.toBeInTheDocument()

    await user.click(applyPullButton)
    await waitFor(() => {
      expect(mockPullWorkflows).toHaveBeenNthCalledWith(3, {
        commit_sha: commitSha,
        sync_schedules: false,
        catalog_mappings: [],
        mcp_integration_mappings: [
          {
            source_mcp_integration_id: sourceMcpId,
            target_mcp_integration_id: targetMcpId,
          },
        ],
      })
    })
  })

  it("clears obsolete MCP integration choices when a later preview has no candidates", async () => {
    const user = userEvent.setup()
    const commitSha = "f".repeat(40)
    const requirement = createMcpMappingRequirement(
      "44444444-4444-4444-4444-444444444444",
      "55555555-5555-5555-5555-555555555555"
    )
    const unresolvedPreview: PullResult = {
      success: false,
      commit_sha: commitSha,
      workflows_found: 0,
      workflows_imported: 0,
      diagnostics: [],
      message: "Choose the target MCP integration before applying this pull.",
      resource_diffs: [],
      mcp_integration_mapping_requirements: [requirement],
    }
    const unavailablePreview: PullResult = {
      ...unresolvedPreview,
      message: "No matching MCP integrations are available.",
      mcp_integration_mapping_requirements: [],
    }
    const connectedWorkspace = setupHooks({
      gitRepoUrl: repositories[0].git_url,
      branches: [{ name: "main", is_default: true }],
      commits: [
        {
          sha: commitSha,
          message: "Import shared MCP integration",
          author: "Test Author",
          author_email: "author@example.com",
          date: "2026-08-11T12:00:00Z",
        },
      ],
    })
    mockPullWorkflows
      .mockResolvedValueOnce(unresolvedPreview)
      .mockResolvedValueOnce(unavailablePreview)

    render(<WorkspaceSyncSettings workspace={connectedWorkspace} />)

    await user.click(screen.getByRole("tab", { name: "Pull" }))
    await user.click(screen.getByRole("button", { name: "Preview changes" }))
    expect(
      screen.getByLabelText("Target MCP integration for shared-mcp")
    ).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: "Preview changes" }))

    await waitFor(() => {
      expect(
        screen.queryByLabelText("Target MCP integration for shared-mcp")
      ).not.toBeInTheDocument()
    })
  })

  it("disables apply when an MCP choice changes after a catalog and MCP preview", async () => {
    const user = userEvent.setup()
    const commitSha = "0".repeat(40)
    const sourceCatalogId = "11111111-1111-1111-1111-111111111111"
    const targetCatalogId = "22222222-2222-2222-2222-222222222222"
    const sourceMcpId = "44444444-4444-4444-4444-444444444444"
    const targetMcpId = "55555555-5555-5555-5555-555555555555"
    const replacementMcpId = "66666666-6666-6666-6666-666666666666"
    const mcpRequirement: McpIntegrationMappingRequirement = {
      ...createMcpMappingRequirement(sourceMcpId, targetMcpId),
      candidates: [
        {
          mcp_integration_id: targetMcpId,
          slug: "shared-mcp-east",
          name: "Shared MCP East",
          server_type: "http",
          auth_type: "oauth",
        },
        {
          mcp_integration_id: replacementMcpId,
          slug: "shared-mcp-west",
          name: "Shared MCP West",
          server_type: "sse",
          auth_type: "api_key",
        },
      ],
    }
    const blockedPreview: PullResult = {
      success: false,
      commit_sha: commitSha,
      workflows_found: 0,
      workflows_imported: 0,
      diagnostics: [],
      message: "Choose targets before applying this pull.",
      resource_diffs: [],
      catalog_mapping_requirements: [
        createCatalogMappingRequirement(sourceCatalogId, targetCatalogId),
      ],
      mcp_integration_mapping_requirements: [mcpRequirement],
    }
    // Requirements persist so the selects stay mounted after a valid preview.
    const resolvedPreview: PullResult = {
      ...blockedPreview,
      success: true,
      message: "Dry run completed - 1 resource change(s) detected",
    }
    const connectedWorkspace = setupHooks({
      gitRepoUrl: repositories[0].git_url,
      branches: [{ name: "main", is_default: true }],
      commits: [
        {
          sha: commitSha,
          message: "Import shared references",
          author: "Test Author",
          author_email: "author@example.com",
          date: "2026-08-11T12:00:00Z",
        },
      ],
    })
    mockPullWorkflows
      .mockResolvedValueOnce(blockedPreview)
      .mockResolvedValueOnce(resolvedPreview)

    render(<WorkspaceSyncSettings workspace={connectedWorkspace} />)

    await user.click(screen.getByRole("tab", { name: "Pull" }))
    await user.click(screen.getByRole("button", { name: "Preview changes" }))

    const applyPullButton = screen.getByRole("button", { name: "Apply pull" })
    expect(applyPullButton).toBeDisabled()

    await user.click(screen.getByLabelText("Target model for shared-model"))
    await user.click(
      screen.getByRole("option", {
        name: "Provider East · east.models.example.com",
      })
    )
    await user.click(
      screen.getByLabelText("Target MCP integration for shared-mcp")
    )
    await user.click(
      screen.getByRole("option", { name: "Shared MCP East (http · oauth)" })
    )

    await user.click(screen.getByRole("button", { name: "Preview changes" }))
    await waitFor(() => {
      expect(mockPullWorkflows).toHaveBeenNthCalledWith(2, {
        commit_sha: commitSha,
        dry_run: true,
        sync_schedules: false,
        catalog_mappings: [
          {
            source_catalog_id: sourceCatalogId,
            target_catalog_id: targetCatalogId,
          },
        ],
        mcp_integration_mappings: [
          {
            source_mcp_integration_id: sourceMcpId,
            target_mcp_integration_id: targetMcpId,
          },
        ],
      })
    })
    expect(applyPullButton).toBeEnabled()

    // Changing only the MCP selection must invalidate the validated preview.
    await user.click(
      screen.getByLabelText("Target MCP integration for shared-mcp")
    )
    await user.click(
      screen.getByRole("option", { name: "Shared MCP West (sse · api_key)" })
    )

    expect(applyPullButton).toBeDisabled()
    // Both the catalog and MCP sections render this warning once each.
    expect(
      screen.getAllByText(
        "Preview changes again to validate these choices before applying."
      )
    ).toHaveLength(2)
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
