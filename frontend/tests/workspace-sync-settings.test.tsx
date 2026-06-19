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
  repositoryHook = {},
}: {
  gitRepoUrl?: string | null
  repositoryHook?: Partial<ReturnType<typeof useGitHubAppRepositories>>
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
          git_repo_url: `${repositories[1].git_url}@trunk`,
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

  it("keeps explicit select mode for an existing custom git URL", async () => {
    const user = userEvent.setup()
    const customUrl =
      "git+ssh://git@github.com/test-org/custom-repo.git@feature/custom"
    const { rerender } = render(
      <WorkspaceSyncSettings
        workspace={setupHooks({ gitRepoUrl: customUrl })}
      />
    )

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

  it("ignores cached app repositories when the repository query errors", () => {
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

    expect(screen.getByLabelText("Remote repository URL")).toBeInTheDocument()
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "Select" })
    ).not.toBeInTheDocument()
    expect(
      screen.getByText(
        "Could not load GitHub App repositories. Enter a git+ssh URL manually."
      )
    ).toBeInTheDocument()
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
