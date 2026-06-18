/**
 * @jest-environment jsdom
 */

import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { GitHubAppRepository, WorkspaceRead } from "@/client"
import { WorkspaceSyncSettings } from "@/components/settings/workspace-sync-settings"
import { useGitHubAppRepositories, useWorkspaceSettings } from "@/lib/hooks"

const mockUpdateWorkspace = jest.fn()

jest.mock("@/lib/hooks", () => ({
  useGitHubAppRepositories: jest.fn(),
  useWorkspaceSettings: jest.fn(),
}))

jest.mock("@/components/organization/workflow-pull-dialog", () => ({
  WorkflowPullDialog: () => null,
}))

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
}: {
  gitRepoUrl?: string | null
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
  } as ReturnType<typeof useGitHubAppRepositories>)

  return {
    ...workspace,
    settings: {
      ...workspace.settings,
      git_repo_url: gitRepoUrl,
    },
  }
}

describe("WorkspaceSyncSettings", () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockUpdateWorkspace.mockResolvedValue(undefined)
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
          git_repo_url: customUrl,
        },
      })
    })
  })

  it("opens in manual mode for an existing custom git URL", async () => {
    const customUrl =
      "git+ssh://git@github.com/test-org/custom-repo.git@feature/custom"

    render(
      <WorkspaceSyncSettings
        workspace={setupHooks({ gitRepoUrl: customUrl })}
      />
    )

    await waitFor(() => {
      expect(screen.getByDisplayValue(customUrl)).toBeInTheDocument()
    })
    expect(screen.getByRole("button", { name: "Manual" })).toHaveAttribute(
      "aria-current",
      "true"
    )
  })
})
