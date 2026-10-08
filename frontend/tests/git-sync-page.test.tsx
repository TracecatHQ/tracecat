/**
 * @jest-environment jsdom
 */

import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { ReactElement } from "react"
import type {
  GitBranchInfo,
  GitCommitInfo,
  PullResult,
  WorkspaceRead,
  WorkspaceSyncExportPreview,
} from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { Toast, ToastProvider, ToastViewport } from "@/components/ui/toast"
import { TooltipProvider } from "@/components/ui/tooltip"
import { toast } from "@/components/ui/use-toast"
import { GitSyncView } from "@/components/workspace-sync/git-sync-view"
import { useGitSyncProviders } from "@/hooks/use-git-sync-providers"
import {
  useRepositoryBranches,
  useRepositoryCommits,
  useWorkflowSync,
  useWorkspaceSyncExport,
  useWorkspaceSyncExportPreview,
} from "@/hooks/use-workspace-sync"
import { useGitHubAppRepositories, useWorkspaceSettings } from "@/lib/hooks"

const mockExportWorkspace = jest.fn()
const mockPullWorkflows = jest.fn()
const mockRefetchExportPreview = jest.fn()

jest.mock("@/lib/hooks", () => ({
  useGitHubAppRepositories: jest.fn(),
  useWorkspaceSettings: jest.fn(),
}))

jest.mock("@/hooks/use-git-sync-providers", () => ({
  useGitSyncProviders: jest.fn(),
}))

jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: jest.fn(),
}))

jest.mock("@/hooks/use-workspace-sync", () => ({
  useRepositoryBranches: jest.fn(),
  useRepositoryCommits: jest.fn(),
  useWorkflowSync: jest.fn(),
  useWorkspaceSyncExport: jest.fn(),
  useWorkspaceSyncExportPreview: jest.fn(),
}))

jest.mock("@/lib/query", () => ({
  useQueryClient: () => ({ invalidateQueries: jest.fn() }),
}))

jest.mock("@/components/ui/sidebar", () => ({
  SidebarTrigger: () => null,
}))

jest.mock("@/components/ui/use-toast", () => ({
  toast: jest.fn(),
}))

beforeAll(() => {
  for (const name of [
    "hasPointerCapture",
    "setPointerCapture",
    "releasePointerCapture",
    "scrollIntoView",
  ] as const) {
    if (!HTMLElement.prototype[name]) {
      Object.defineProperty(HTMLElement.prototype, name, {
        value: () => false,
      })
    }
  }
})

const REPO_URL = "git+ssh://git@github.com/TracecatHQ/detections.git"
const LATEST_SHA = "1c52757aa0000000000000000000000000000000"

const COMMITS: GitCommitInfo[] = [
  {
    sha: LATEST_SHA,
    message: "Update detections",
    author: "Example Author",
    author_email: "author@example.com",
    date: new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString(),
  },
]

function setup({
  gitRepoUrl = REPO_URL,
  branches = [{ name: "main", is_default: true }],
  commits = COMMITS,
  preview,
}: {
  gitRepoUrl?: string | null
  branches?: GitBranchInfo[]
  commits?: GitCommitInfo[]
  preview?: WorkspaceSyncExportPreview
} = {}): WorkspaceRead {
  jest
    .mocked(useGitSyncProviders)
    .mockReturnValue({ kind: "known", providers: [{ id: "github" }] })
  jest.mocked(useScopeCheck).mockReturnValue(false)
  jest.mocked(useWorkspaceSettings).mockReturnValue({
    updateWorkspace: jest.fn(),
    isUpdating: false,
    deleteWorkspace: jest.fn(),
    isDeleting: false,
  } as ReturnType<typeof useWorkspaceSettings>)
  jest.mocked(useGitHubAppRepositories).mockReturnValue({
    repositories: [],
    repositoriesIsLoading: false,
    repositoriesError: null,
    refetchRepositories: jest.fn(),
  } as unknown as ReturnType<typeof useGitHubAppRepositories>)
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
    preview,
    previewIsLoading: false,
    previewError: null,
    refetchPreview: mockRefetchExportPreview,
  } as ReturnType<typeof useWorkspaceSyncExportPreview>)
  return {
    id: "workspace-1",
    name: "Workspace 1",
    organization_id: "org-1",
    settings: {
      git_provider: "github",
      git_repo_url: gitRepoUrl,
      effective_allowed_attachment_extensions: [],
      effective_allowed_attachment_mime_types: [],
    },
  }
}

function renderWithTooltips(ui: ReactElement) {
  return render(<TooltipProvider>{ui}</TooltipProvider>)
}

beforeEach(() => {
  jest.clearAllMocks()
  mockRefetchExportPreview.mockResolvedValue({ error: null })
})

const PUSH_PREVIEW: WorkspaceSyncExportPreview = {
  resource_counts: { workflow: 2 },
  files: [
    "workflows/phishing-triage/definition.yml",
    "workflows/daily-sweep/definition.yml",
  ],
  resources: [
    {
      resource_type: "workflow",
      source_id: "phishing-triage",
      name: "Phishing triage",
      path: "workflows/phishing-triage/definition.yml",
    },
    {
      resource_type: "workflow",
      source_id: "daily-sweep",
      name: "Daily sweep",
      path: "workflows/daily-sweep/definition.yml",
    },
  ],
  resource_diffs: [
    {
      resource_type: "workflow",
      source_id: "phishing-triage",
      source_path: "workflows/phishing-triage/definition.yml",
      change_type: "modified",
      title: "Phishing triage",
      diff: "--- a\n+++ b\n@@ -1,2 +1,2 @@\n title: Phishing triage\n-score: 0.7\n+score: 0.82",
    },
    {
      resource_type: "workflow",
      source_id: "legacy-check",
      source_path: "workflows/legacy-check/definition.yml",
      change_type: "deleted",
      title: "Legacy check",
      diff: "@@ -1 +0,0 @@\n-title: Legacy check",
    },
  ],
}

describe("GitSyncView connected", () => {
  it("puts the tabs and repo status in the header and the target in the footer", () => {
    renderWithTooltips(
      <GitSyncView workspace={setup()} canSync canManageConnection />
    )

    expect(screen.queryByRole("heading", { level: 2 })).not.toBeInTheDocument()
    expect(screen.getByRole("tab", { name: "Push" })).toHaveAttribute(
      "data-state",
      "active"
    )
    expect(screen.getByText("TracecatHQ/detections")).toBeInTheDocument()
    expect(screen.getByText("Connected")).toBeInTheDocument()

    const header = screen.getByRole("banner")
    expect(within(header).getByRole("tablist")).toBeInTheDocument()
    expect(within(header).getByText("Connected")).toBeInTheDocument()

    const bar = screen.getByRole("group", { name: "Push actions" })
    expect(bar).toHaveTextContent(/^Into/)
    expect(
      within(bar).getByRole("button", { name: "Branch" })
    ).toHaveTextContent(/^sync\/workspace-[0-9a-z]+new$/)
    expect(
      within(bar).getByRole("textbox", { name: "Commit message" })
    ).toHaveValue("Export workspace config")
    expect(
      within(screen.getByRole("tabpanel", { name: "Push" })).getByText(
        "No preview yet"
      )
    ).toBeInTheDocument()
  })

  it("opens the connection panel in place and returns with Back", async () => {
    const user = userEvent.setup()
    renderWithTooltips(
      <GitSyncView workspace={setup()} canSync canManageConnection />
    )

    await user.click(screen.getByRole("button", { name: "Edit connection" }))
    expect(
      screen.getByRole("heading", { name: "Repository connection" })
    ).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: "Back to changes" }))
    expect(
      screen.queryByRole("heading", { name: "Repository connection" })
    ).not.toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: "Edit connection" })
    ).toBeInTheDocument()
  })

  it("hides Edit without workspace update access", () => {
    renderWithTooltips(
      <GitSyncView workspace={setup()} canSync canManageConnection={false} />
    )

    expect(
      screen.queryByRole("button", { name: "Edit connection" })
    ).not.toBeInTheDocument()
    expect(screen.getByText("TracecatHQ/detections")).toBeInTheDocument()
  })

  it("previews a push as a grouped list with inline diffs", async () => {
    const user = userEvent.setup()
    renderWithTooltips(
      <GitSyncView
        workspace={setup({ preview: PUSH_PREVIEW })}
        canSync
        canManageConnection
      />
    )

    expect(screen.queryByText("Included in this push")).not.toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Preview" }))
    expect(mockRefetchExportPreview).toHaveBeenCalledTimes(1)
    expect(
      await screen.findByText("1 modified · 1 deleted, compared with main")
    ).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: "Preview again" })
    ).toBeInTheDocument()
    expect(screen.getByText("Included in this push")).toBeInTheDocument()
    expect(screen.getByText("2 files")).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: /Workflows/ }))
    expect(screen.getByRole("img", { name: "Modified" })).toBeInTheDocument()
    expect(screen.getByRole("img", { name: "Deleted" })).toBeInTheDocument()
    expect(screen.getByText("Unchanged")).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: /Phishing triage/ }))
    expect(screen.getByText("+1")).toBeInTheDocument()
    expect(screen.getByText("−1")).toBeInTheDocument()
    expect(screen.getByText("lines")).toBeInTheDocument()
    expect(screen.queryByRole("link")).not.toBeInTheDocument()

    const bar = screen.getByRole("group", { name: "Push actions" })
    expect(bar).toHaveClass("sticky", "bottom-0", "border-t", "bg-background")
    expect(
      within(bar).getByRole("button", { name: "Push 2 and open PR" })
    ).toBeEnabled()
  })

  it("pushes and opens a pull request with a View PR toast", async () => {
    const user = userEvent.setup()
    const prUrl = "https://github.com/TracecatHQ/detections/pull/42"
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
    renderWithTooltips(
      <GitSyncView workspace={setup()} canSync canManageConnection />
    )

    await user.click(screen.getByRole("button", { name: "Push and open PR" }))

    await waitFor(() =>
      expect(mockExportWorkspace).toHaveBeenCalledWith(
        expect.objectContaining({
          message: "Export workspace config",
          branch: expect.stringMatching(/^sync\/workspace-/),
          create_pr: true,
          include_schedules: false,
        })
      )
    )
    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith(
        expect.objectContaining({ title: "PR ready" })
      )
    )
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
  })

  it("creates a named branch from the picker", async () => {
    const user = userEvent.setup()
    renderWithTooltips(
      <GitSyncView workspace={setup()} canSync canManageConnection />
    )

    await user.click(screen.getByRole("button", { name: "Branch" }))
    expect(screen.getByText("New branch from main")).toBeInTheDocument()
    const input = screen.getByLabelText("New branch from main")
    await user.clear(input)
    await user.type(input, "sync/oct-08")
    await user.click(screen.getByRole("button", { name: "Create" }))

    expect(screen.getByRole("button", { name: "Branch" })).toHaveTextContent(
      "sync/oct-08new"
    )
    expect(
      screen.getByRole("button", { name: "Push and open PR" })
    ).toBeEnabled()
  })

  it("blocks pushing to the default branch", async () => {
    const user = userEvent.setup()
    renderWithTooltips(
      <GitSyncView workspace={setup()} canSync canManageConnection />
    )

    await user.click(screen.getByRole("button", { name: "Branch" }))
    await user.click(screen.getByRole("option", { name: /main/ }))

    expect(screen.getByRole("button", { name: "Branch" })).toHaveTextContent(
      "maindefault"
    )
    expect(
      screen.getByText(
        "Pushing to main directly is off. Pick or create another branch."
      )
    ).toBeInTheDocument()
    const bar = screen.getByRole("group", { name: "Push actions" })
    expect(
      within(bar).getByRole("button", { name: "Pick another branch" })
    ).toBeDisabled()
    expect(
      within(bar).getByRole("button", { name: "Push options" })
    ).toBeDisabled()
  })

  it("pushes only, without a pull request, from the button menu", async () => {
    const user = userEvent.setup()
    mockExportWorkspace.mockResolvedValue({
      commit: {
        status: "committed",
        sha: "b".repeat(40),
        ref: "release",
        base_ref: "main",
        pr_url: null,
        message: "Export workspace config",
      },
      files: ["tracecat.json"],
    })
    renderWithTooltips(
      <GitSyncView
        workspace={setup({
          branches: [
            { name: "main", is_default: true },
            { name: "release", is_default: false },
          ],
        })}
        canSync
        canManageConnection
      />
    )

    await user.click(screen.getByRole("button", { name: "Branch" }))
    await user.click(screen.getByRole("option", { name: /release/ }))

    await user.click(screen.getByRole("button", { name: "Push options" }))
    await user.click(screen.getByRole("menuitemradio", { name: /Push only/ }))

    const bar = screen.getByRole("group", { name: "Push actions" })
    await user.click(
      within(bar).getByRole("button", { name: "Push to release" })
    )
    await waitFor(() =>
      expect(mockExportWorkspace).toHaveBeenCalledWith(
        expect.objectContaining({ branch: "release", create_pr: false })
      )
    )
  })

  it("gates the pull on a re-previewed model mapping", async () => {
    const user = userEvent.setup()
    const sourceCatalogId = "11111111-1111-1111-1111-111111111111"
    const targetCatalogId = "22222222-2222-2222-2222-222222222222"
    const ambiguousPreview: PullResult = {
      success: false,
      commit_sha: LATEST_SHA,
      workflows_found: 0,
      workflows_imported: 0,
      diagnostics: [],
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
          ],
          affected_presets: [],
          affected_workflows: [],
        },
      ],
    }
    const resolvedPreview: PullResult = {
      ...ambiguousPreview,
      success: true,
      message: "Dry run completed - 1 resource change(s) detected",
      catalog_mapping_requirements: [],
      resource_counts: { workflow: { found: 1, imported: 0 } },
      resource_diffs: [
        {
          resource_type: "workflow",
          source_id: "phishing-triage",
          source_path: "workflows/phishing-triage/definition.yml",
          change_type: "modified",
          title: "Phishing triage",
          diff: "@@ -1 +1 @@\n-old\n+new",
        },
      ],
    }
    mockPullWorkflows
      .mockResolvedValueOnce(ambiguousPreview)
      .mockResolvedValueOnce(resolvedPreview)
      .mockResolvedValueOnce({
        ...resolvedPreview,
        message: "Imported",
        resource_counts: { workflow: { found: 1, imported: 1 } },
        resources: [
          {
            resource_type: "workflow",
            source_id: "phishing-triage",
            name: "Phishing triage",
            path: "workflows/phishing-triage/definition.yml",
          },
        ],
      })
    renderWithTooltips(
      <GitSyncView workspace={setup()} canSync canManageConnection />
    )

    await user.click(screen.getByRole("tab", { name: "Pull" }))
    const bar = screen.getByRole("group", { name: "Pull actions" })
    expect(bar).toHaveTextContent(/^From/)
    const pullPanel = screen.getByRole("tabpanel", { name: "Pull" })
    expect(within(pullPanel).getByText("No preview yet")).toBeInTheDocument()
    await user.hover(
      within(bar).getByRole("button", { name: "About overwriting schedules" })
    )
    expect(
      (
        await screen.findAllByText(
          "Existing resources with matching IDs are overwritten. Schedules are preserved."
        )
      ).length
    ).toBeGreaterThan(0)
    await user.unhover(
      within(bar).getByRole("button", { name: "About overwriting schedules" })
    )
    const commitField = within(bar).getByRole("combobox", { name: "Commit" })
    expect(commitField).toHaveTextContent(
      "1c52757Update detections· about 2 hours agolatest"
    )
    await user.click(commitField)
    await user.type(
      screen.getByPlaceholderText("Search by message, author, or SHA"),
      "Example"
    )
    expect(
      screen.getByRole("option", { name: /Update detections/ })
    ).toHaveTextContent(
      "EAUpdate detectionslatest1c52757·Example Author·about 2 hours ago"
    )
    await user.keyboard("{Escape}")
    expect(bar).toHaveClass("sticky", "bottom-0")
    const applyButton = within(bar).getByRole("button", {
      name: "Pull into Workspace 1",
    })
    expect(applyButton).toBeDisabled()

    await user.click(screen.getByRole("button", { name: "Preview" }))
    // A preview that needs matches opens the dialog on its own.
    const dialog = await screen.findByRole("dialog", {
      name: "Match 1 reference to pull",
    })
    expect(
      screen.getByText(
        "Pull is blocked: 1 reference in this commit needs a match in Workspace 1."
      )
    ).toBeInTheDocument()
    expect(applyButton).toBeDisabled()
    const save = within(dialog).getByRole("button", {
      name: "Save and preview again",
    })
    expect(save).toBeDisabled()
    expect(within(dialog).getByText("0 of 1 matched")).toBeInTheDocument()

    await user.click(
      within(dialog).getByLabelText("Target model for shared-model")
    )
    await user.click(
      screen.getByRole("option", {
        name: "Provider East · east.models.example.com",
      })
    )
    expect(within(dialog).getByText("1 of 1 matched")).toBeInTheDocument()
    expect(applyButton).toBeDisabled()

    await user.click(save)
    expect(
      await screen.findByText("Matches checked for shared-model.")
    ).toBeInTheDocument()
    const pullButton = await within(bar).findByRole("button", {
      name: "Pull 1 into Workspace 1",
    })
    expect(pullButton).toBeEnabled()
    expect(screen.getByText("Included in this pull")).toBeInTheDocument()
    expect(
      screen.getByText("1 modified from 1c52757 on main")
    ).toBeInTheDocument()

    await user.click(pullButton)
    await waitFor(() =>
      expect(mockPullWorkflows).toHaveBeenNthCalledWith(3, {
        commit_sha: LATEST_SHA,
        sync_schedules: false,
        catalog_mappings: [
          {
            source_catalog_id: sourceCatalogId,
            target_catalog_id: targetCatalogId,
          },
        ],
        mcp_integration_mappings: [],
      })
    )
    expect(
      await screen.findByText("Pulled 1 into Workspace 1")
    ).toBeInTheDocument()
    expect(screen.getByText("What changed")).toBeInTheDocument()
    expect(screen.getByText("Phishing triage")).toBeInTheDocument()
    expect(screen.getByText("1 of 1")).toBeInTheDocument()
    expect(
      within(bar).getByRole("button", { name: "Preview again" })
    ).toBeInTheDocument()
  })
})
