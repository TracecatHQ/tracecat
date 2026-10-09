/**
 * @jest-environment jsdom
 */

import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type {
  GitBranchInfo,
  GitHubAppRepository,
  VcsProvider,
  WorkspaceRead,
} from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { GitSyncConnectionPanel } from "@/components/workspace-sync/git-sync-connection"
import { GitSyncView } from "@/components/workspace-sync/git-sync-view"
import {
  type GitSyncProvidersState,
  useGitSyncProviders,
} from "@/hooks/use-git-sync-providers"
import {
  useRepositoryBranches,
  useRepositoryCommits,
} from "@/hooks/use-workspace-sync"
import { useGitHubAppRepositories, useWorkspaceSettings } from "@/lib/hooks"

const mockUpdateWorkspace = jest.fn()

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
}))

jest.mock("@/lib/query", () => ({
  useQueryClient: () => ({ invalidateQueries: jest.fn() }),
}))

jest.mock("@/components/ui/sidebar", () => ({
  SidebarTrigger: () => null,
}))

jest.mock("@/components/workspace-sync/git-sync-push", () => ({
  GitSyncPushTab: () => <div>Push tab</div>,
}))

jest.mock("@/components/workspace-sync/git-sync-pull", () => ({
  GitSyncPullTab: () => <div>Pull tab</div>,
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

const repositories: GitHubAppRepository[] = [
  {
    id: 1,
    name: "detections",
    full_name: "TracecatHQ/detections",
    private: true,
    default_branch: "main",
    git_url: "git+ssh://git@github.com/TracecatHQ/detections.git",
    html_url: "https://github.com/TracecatHQ/detections",
    installation_id: 1001,
    installation_account: "TracecatHQ",
    installation_account_type: "Organization",
  },
  {
    id: 2,
    name: "playbooks",
    full_name: "TracecatHQ/playbooks",
    private: false,
    default_branch: "trunk",
    git_url: "git+ssh://git@github.com/TracecatHQ/playbooks.git",
    html_url: "https://github.com/TracecatHQ/playbooks",
    installation_id: 1001,
    installation_account: "TracecatHQ",
    installation_account_type: "Organization",
  },
]

const GITHUB_ONLY: GitSyncProvidersState = {
  kind: "known",
  providers: [{ id: "github" }],
}
const GITLAB_ONLY: GitSyncProvidersState = {
  kind: "known",
  providers: [{ id: "gitlab", host: "gitlab.example.com" }],
}

function setup({
  gitRepoUrl = null,
  gitProvider = null,
  providers = GITHUB_ONLY,
  scopes = [],
  repositoryHook = {},
  branches = [],
}: {
  gitRepoUrl?: string | null
  gitProvider?: VcsProvider | null
  providers?: GitSyncProvidersState
  scopes?: string[]
  repositoryHook?: Partial<ReturnType<typeof useGitHubAppRepositories>>
  branches?: GitBranchInfo[]
} = {}): WorkspaceRead {
  jest.mocked(useGitSyncProviders).mockReturnValue(providers)
  jest
    .mocked(useScopeCheck)
    .mockImplementation((scope?: string) => scopes.includes(scope ?? ""))
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
    commits: [],
    commitsIsLoading: false,
    commitsError: null,
  } as ReturnType<typeof useRepositoryCommits>)
  return {
    id: "workspace-1",
    name: "Workspace 1",
    organization_id: "org-1",
    settings: {
      git_provider: gitProvider,
      git_repo_url: gitRepoUrl,
      effective_allowed_attachment_extensions: [],
      effective_allowed_attachment_mime_types: [],
    },
  }
}

function repositoryInput() {
  return screen.getByRole("textbox", { name: "Repository" })
}

function providerSelect() {
  return screen.getByRole("combobox", { name: "Provider" })
}

beforeEach(() => {
  jest.clearAllMocks()
  mockUpdateWorkspace.mockResolvedValue(undefined)
})

describe("GitSyncConnectionPanel remote line", () => {
  it("connects a typed GitHub repository on Enter", async () => {
    const user = userEvent.setup()
    const onSaved = jest.fn()
    render(<GitSyncConnectionPanel workspace={setup()} onSaved={onSaved} />)

    expect(
      screen.getByRole("heading", { name: "Connect a repository" })
    ).toBeInTheDocument()
    // One provider: shown, but not a choice.
    expect(providerSelect()).toHaveTextContent("GitHub")
    expect(providerSelect()).toBeDisabled()
    expect(
      screen.getByText("Uses the GitHub App on TracecatHQ.")
    ).toBeInTheDocument()

    await user.type(repositoryInput(), "TracecatHQ/detections")
    expect(repositoryInput().nextElementSibling).toHaveTextContent(
      "TracecatHQ/detections on github.com"
    )
    await user.keyboard("{Enter}")

    await waitFor(() =>
      expect(mockUpdateWorkspace).toHaveBeenCalledWith({
        settings: {
          git_provider: "github",
          git_repo_url: "git+ssh://git@github.com/TracecatHQ/detections.git",
        },
      })
    )
    expect(onSaved).toHaveBeenCalled()
  })

  it("fills the input from the app repository list", async () => {
    const user = userEvent.setup()
    render(<GitSyncConnectionPanel workspace={setup()} />)

    expect(screen.getByText("Private")).toBeInTheDocument()
    expect(screen.getByText("Public")).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: /playbooks/ }))
    expect(repositoryInput()).toHaveValue("TracecatHQ/playbooks")
    await user.click(screen.getByRole("button", { name: "Connect" }))

    await waitFor(() =>
      expect(mockUpdateWorkspace).toHaveBeenCalledWith({
        settings: {
          git_provider: "github",
          git_repo_url:
            "git+ssh://git@github.com/TracecatHQ/playbooks.git@trunk",
        },
      })
    )
  })

  it("filters the list and reports no match", async () => {
    const user = userEvent.setup()
    render(<GitSyncConnectionPanel workspace={setup()} />)

    await user.type(repositoryInput(), "https://github.com/TracecatHQ/play")
    expect(
      screen.queryByRole("button", { name: /detections/ })
    ).not.toBeInTheDocument()
    await user.clear(repositoryInput())
    await user.type(repositoryInput(), "zzz")
    expect(
      screen.getByText("No match. Paste the full URL to check it.")
    ).toBeInTheDocument()
  })

  it.each([
    [[], "Ask an org admin to add it to the app's repositories."],
    [
      ["org:settings:update"],
      "Add it to the app's repositories on GitHub, then try again.",
    ],
  ])("blocks repositories the GitHub App can't reach", async (scopes, hint) => {
    const user = userEvent.setup()
    render(<GitSyncConnectionPanel workspace={setup({ scopes })} />)

    await user.type(repositoryInput(), "TracecatHQ/elsewhere{Enter}")

    expect(screen.getByRole("alert")).toHaveTextContent(
      `The GitHub App can't reach TracecatHQ/elsewhere. ${hint}`
    )
    expect(mockUpdateWorkspace).not.toHaveBeenCalled()
  })

  it("still saves a pasted URL when the list can't load", async () => {
    const user = userEvent.setup()
    render(
      <GitSyncConnectionPanel
        workspace={setup({
          repositoryHook: {
            repositories: undefined,
            repositoriesError: new Error("forbidden"),
          },
        })}
      />
    )

    expect(
      screen.getByText(
        "Couldn't load the list. A pasted repository URL still works."
      )
    ).toBeInTheDocument()
    await user.type(
      repositoryInput(),
      "https://github.com/TracecatHQ/elsewhere/tree/main{Enter}"
    )
    await waitFor(() =>
      expect(mockUpdateWorkspace).toHaveBeenCalledWith({
        settings: {
          git_provider: "github",
          git_repo_url: "git+ssh://git@github.com/TracecatHQ/elsewhere.git",
        },
      })
    )
  })

  it("waits for the app's repository list before connecting", async () => {
    const user = userEvent.setup()
    render(
      <GitSyncConnectionPanel
        workspace={setup({
          repositoryHook: {
            repositories: undefined,
            repositoriesIsLoading: true,
          },
        })}
      />
    )

    await user.type(repositoryInput(), "TracecatHQ/elsewhere{Enter}")

    expect(screen.getByRole("button", { name: "Connect" })).toBeDisabled()
    expect(mockUpdateWorkspace).not.toHaveBeenCalled()
  })

  it("shows skeleton rows while the list loads", () => {
    render(
      <GitSyncConnectionPanel
        workspace={setup({
          repositoryHook: {
            repositories: undefined,
            repositoriesIsLoading: true,
          },
        })}
      />
    )
    expect(screen.getByLabelText("Loading repositories")).toBeInTheDocument()
  })

  it("uses the organization's GitLab host and rejects other hosts", async () => {
    const user = userEvent.setup()
    render(
      <GitSyncConnectionPanel workspace={setup({ providers: GITLAB_ONLY })} />
    )

    expect(useGitHubAppRepositories).toHaveBeenCalledWith("workspace-1", {
      enabled: false,
    })
    expect(providerSelect()).toHaveTextContent("GitLabgitlab.example.com")
    expect(repositoryInput()).toHaveAttribute("placeholder", "group/project")
    expect(
      screen.getByText(
        "Paste the project URL from gitlab.example.com, or type group/project. Nested groups work."
      )
    ).toBeInTheDocument()
    expect(
      screen.getByText("Uses the organization's GitLab token.")
    ).toBeInTheDocument()

    await user.type(
      repositoryInput(),
      "https://gitlab.com/group/project{Enter}"
    )
    expect(screen.getByRole("alert")).toHaveTextContent(
      "gitlab.com isn't set up for this organization. GitLab here is gitlab.example.com."
    )

    await user.clear(repositoryInput())
    await user.type(repositoryInput(), "group/sub/project{Enter}")
    await waitFor(() =>
      expect(mockUpdateWorkspace).toHaveBeenCalledWith({
        settings: {
          git_provider: "gitlab",
          git_repo_url:
            "git+ssh://git@gitlab.example.com/group/sub/project.git",
        },
      })
    )
  })

  it("asks where the repository is when several providers are set up", async () => {
    const user = userEvent.setup()
    render(
      <GitSyncConnectionPanel
        workspace={setup({
          providers: {
            kind: "known",
            providers: [
              { id: "github" },
              { id: "gitlab", host: "gitlab.example.com" },
            ],
          },
        })}
      />
    )

    expect(providerSelect()).toHaveTextContent(
      "Choose where the repository lives"
    )
    expect(providerSelect()).toBeEnabled()
    expect(repositoryInput()).toBeDisabled()
    expect(screen.getByRole("button", { name: "Connect" })).toBeDisabled()

    await user.click(providerSelect())
    await user.click(await screen.findByRole("option", { name: /GitLab/ }))

    expect(providerSelect()).toHaveTextContent("GitLabgitlab.example.com")
    expect(repositoryInput()).toBeEnabled()
    expect(repositoryInput()).toHaveAttribute("placeholder", "group/project")
  })

  it("lets viewers without provider status pick a provider explicitly", async () => {
    const user = userEvent.setup()
    render(
      <GitSyncConnectionPanel
        workspace={setup({ providers: { kind: "unknown" } })}
      />
    )

    expect(providerSelect()).toHaveTextContent("GitHub")
    expect(
      screen.getByText("Uses the GitHub App on TracecatHQ.")
    ).toBeInTheDocument()
    await user.click(providerSelect())
    await user.click(await screen.findByRole("option", { name: /GitLab/ }))

    expect(
      screen.getByText("Uses the organization's GitLab token.")
    ).toBeInTheDocument()
    expect(
      screen.getByText("Paste the full repository URL from GitLab.")
    ).toBeInTheDocument()
    await user.type(repositoryInput(), "group/project{Enter}")
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Paste the full repository URL from GitLab."
    )
    expect(mockUpdateWorkspace).not.toHaveBeenCalled()
  })

  it.each([
    [
      ["org:settings:update"],
      "Set up GitHub, GitLab or Bitbucket once in Organization › Git providers. Every workspace can then connect a repository.",
    ],
    [
      [],
      "Ask an org admin to set one up in Organization › Git providers. You can connect a repository here once they have.",
    ],
  ])("blocks the flow when the org has no provider", (scopes, body) => {
    render(
      <GitSyncConnectionPanel
        workspace={setup({
          scopes,
          providers: { kind: "known", providers: [] },
        })}
      />
    )

    expect(
      screen.getByText("Your organization hasn't set up a Git provider")
    ).toBeInTheDocument()
    expect(screen.getByText(body)).toBeInTheDocument()
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument()
    const link = screen.queryByRole("link", { name: /Set up a Git provider/ })
    if (scopes.length > 0) {
      expect(link).toHaveAttribute("href", "/organization/vcs")
    } else {
      expect(link).not.toBeInTheDocument()
    }
  })

  it("shows no remote line while provider status loads", () => {
    render(
      <GitSyncConnectionPanel
        workspace={setup({ providers: { kind: "loading" } })}
      />
    )
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument()
  })

  it("prefills the full URL when the stored connection pins a ref", async () => {
    const user = userEvent.setup()
    const url = "git+ssh://git@github.com/TracecatHQ/detections.git@release"
    render(
      <GitSyncConnectionPanel
        workspace={setup({ gitRepoUrl: url, gitProvider: "github" })}
      />
    )
    await user.click(screen.getByRole("button", { name: "Change" }))
    expect(repositoryInput()).toHaveValue(url)
  })
})

describe("GitSyncConnectionPanel connected repository", () => {
  const url = "git+ssh://git@github.com/TracecatHQ/detections.git"

  it("shows the repository read-only with change and disconnect", () => {
    render(
      <GitSyncConnectionPanel
        workspace={setup({
          gitRepoUrl: url,
          gitProvider: "github",
          branches: [{ name: "main", is_default: true }],
        })}
      />
    )

    expect(
      screen.getByText(
        "Shared by everyone in this workspace. Access uses the organization's GitHub App."
      )
    ).toBeInTheDocument()
    expect(screen.getByText("TracecatHQ/detections")).toBeInTheDocument()
    expect(screen.getByText("Branch").closest("div")).toHaveTextContent(
      "Branchmain"
    )
    expect(screen.queryByText("Pushes")).not.toBeInTheDocument()
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Change" })).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: "Disconnect" })
    ).toBeInTheDocument()
    expect(
      screen.getByText("Stops push and pull for this workspace.")
    ).toBeInTheDocument()
    expect(useGitSyncProviders).toHaveBeenLastCalledWith({ enabled: false })
  })

  it("changes the repository in its row and stays on the page", async () => {
    const user = userEvent.setup()
    const onSaved = jest.fn()
    render(
      <GitSyncConnectionPanel
        workspace={setup({ gitRepoUrl: url, gitProvider: "github" })}
        onSaved={onSaved}
      />
    )

    await user.click(screen.getByRole("button", { name: "Change" }))
    expect(repositoryInput()).toHaveValue("TracecatHQ/detections")
    expect(repositoryInput()).toHaveFocus()
    expect(
      screen.getByText(
        "Changing the repository clears any preview on this page."
      )
    ).toBeInTheDocument()

    // Focus selected the prefill, so typing replaces it.
    await user.keyboard("TracecatHQ/playbooks")
    expect(repositoryInput()).toHaveValue("TracecatHQ/playbooks")
    await user.click(screen.getByRole("button", { name: "Save" }))

    await waitFor(() =>
      expect(mockUpdateWorkspace).toHaveBeenCalledWith({
        settings: {
          git_provider: "github",
          git_repo_url:
            "git+ssh://git@github.com/TracecatHQ/playbooks.git@trunk",
        },
      })
    )
    await waitFor(() =>
      expect(screen.queryByRole("textbox")).not.toBeInTheDocument()
    )
    expect(onSaved).toHaveBeenCalled()
    expect(
      screen.getByRole("heading", { name: "Repository connection" })
    ).toBeInTheDocument()
  })

  it("closes the row editor without saving on cancel or no change", async () => {
    const user = userEvent.setup()
    render(
      <GitSyncConnectionPanel
        workspace={setup({ gitRepoUrl: url, gitProvider: "github" })}
      />
    )

    await user.click(screen.getByRole("button", { name: "Change" }))
    await user.click(screen.getByRole("button", { name: "Save" }))
    await waitFor(() =>
      expect(screen.queryByRole("textbox")).not.toBeInTheDocument()
    )

    await user.click(screen.getByRole("button", { name: "Change" }))
    await user.keyboard("TracecatHQ/playbooks")
    await user.click(screen.getByRole("button", { name: "Cancel" }))
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument()
    expect(screen.getByText("TracecatHQ/detections")).toBeInTheDocument()
    expect(mockUpdateWorkspace).not.toHaveBeenCalled()
  })

  it("pins and unpins the sync branch through the URL ref", async () => {
    const user = userEvent.setup()
    const branches = [
      { name: "main", is_default: true },
      { name: "release", is_default: false },
    ]
    const { rerender } = render(
      <GitSyncConnectionPanel
        workspace={setup({ gitRepoUrl: url, gitProvider: "github", branches })}
      />
    )

    const branchSelect = screen.getByRole("combobox", { name: "Branch" })
    expect(branchSelect).toHaveTextContent("maindefault")
    await user.click(branchSelect)
    await user.click(screen.getByRole("option", { name: "release" }))
    await waitFor(() =>
      expect(mockUpdateWorkspace).toHaveBeenCalledWith({
        settings: { git_repo_url: `${url}@release` },
      })
    )

    mockUpdateWorkspace.mockClear()
    rerender(
      <GitSyncConnectionPanel
        workspace={setup({
          gitRepoUrl: `${url}@release`,
          gitProvider: "github",
          branches,
        })}
      />
    )
    expect(screen.getByRole("combobox", { name: "Branch" })).toHaveTextContent(
      "release"
    )
    await user.click(screen.getByRole("combobox", { name: "Branch" }))
    await user.click(screen.getByRole("option", { name: /main/ }))
    await waitFor(() =>
      expect(mockUpdateWorkspace).toHaveBeenCalledWith({
        settings: { git_repo_url: url },
      })
    )
  })

  it("shows a pin on the default branch as pinned and lets it be cleared", async () => {
    const user = userEvent.setup()
    render(
      <GitSyncConnectionPanel
        workspace={setup({
          gitRepoUrl: `${url}@develop`,
          gitProvider: "github",
          branches: [
            { name: "develop", is_default: true },
            { name: "release", is_default: false },
          ],
        })}
      />
    )

    const branchSelect = screen.getByRole("combobox", { name: "Branch" })
    expect(branchSelect).toHaveTextContent("developpinned")
    await user.click(branchSelect)
    await user.click(screen.getByRole("option", { name: "develop default" }))
    await waitFor(() =>
      expect(mockUpdateWorkspace).toHaveBeenCalledWith({
        settings: { git_repo_url: url },
      })
    )
  })

  it("shows who made the latest commit and when, with the message on hover", () => {
    const workspace = setup({ gitRepoUrl: url, gitProvider: "github" })
    jest.mocked(useRepositoryCommits).mockReturnValue({
      commits: [
        {
          sha: "01f9d62aa0000000000000000000000000000000",
          message: "Add escalation decision\n\nLonger body",
          author: "Example Author",
          author_email: "author@example.com",
          date: new Date(Date.now() - 2 * 24 * 60 * 60 * 1000).toISOString(),
        },
      ],
      commitsIsLoading: false,
      commitsError: null,
    } as ReturnType<typeof useRepositoryCommits>)
    render(<GitSyncConnectionPanel workspace={workspace} />)

    const row = screen.getByText("Latest commit").closest("div")
    expect(row).toHaveTextContent(
      "Latest commit01f9d62 · Example Author · about 2 days ago"
    )
    expect(
      within(row as HTMLElement).getByTitle("Add escalation decision")
    ).toBeInTheDocument()
  })

  it("hides the actions without workspace update access", () => {
    render(
      <GitSyncConnectionPanel
        workspace={setup({ gitRepoUrl: url })}
        canManageConnection={false}
      />
    )

    expect(
      screen.queryByRole("button", { name: "Change" })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "Disconnect" })
    ).not.toBeInTheDocument()
  })

  it("disconnects by clearing only the repository URL", async () => {
    const user = userEvent.setup()
    render(<GitSyncConnectionPanel workspace={setup({ gitRepoUrl: url })} />)

    await user.click(screen.getByRole("button", { name: "Disconnect" }))
    const dialog = await screen.findByRole("alertdialog")
    expect(dialog).toHaveTextContent(
      "Disconnect TracecatHQ/detections?Push and pull stop until a repository is connected again. Nothing in this workspace or the repository is deleted."
    )
    await user.click(within(dialog).getByRole("button", { name: "Disconnect" }))

    await waitFor(() => expect(mockUpdateWorkspace).toHaveBeenCalledTimes(1))
    expect(mockUpdateWorkspace).toHaveBeenCalledWith({
      settings: { git_repo_url: null },
    })
  })

  it("keeps the connection when disconnect is cancelled", async () => {
    const user = userEvent.setup()
    render(<GitSyncConnectionPanel workspace={setup({ gitRepoUrl: url })} />)

    await user.click(screen.getByRole("button", { name: "Disconnect" }))
    const dialog = await screen.findByRole("alertdialog")
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }))

    await waitFor(() =>
      expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument()
    )
    expect(mockUpdateWorkspace).not.toHaveBeenCalled()
  })
})

describe("GitSyncView connection", () => {
  it("asks non-admins to get a repository connected", () => {
    render(
      <GitSyncView workspace={setup()} canSync canManageConnection={false} />
    )

    expect(screen.getByText("No repository connected")).toBeInTheDocument()
    expect(
      screen.getByText(
        "Ask a workspace admin to connect one. Once it's connected, you can push and pull here."
      )
    ).toBeInTheDocument()
    expect(useGitHubAppRepositories).not.toHaveBeenCalled()
  })

  it("renders the remote line inline for admins without a repository", () => {
    render(<GitSyncView workspace={setup()} canSync canManageConnection />)

    expect(
      screen.getByRole("heading", { name: "Connect a repository" })
    ).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Connect" })).toBeInTheDocument()
  })

  it("opens the connection from the header and goes back to changes", async () => {
    const user = userEvent.setup()
    render(
      <GitSyncView
        workspace={setup({
          gitRepoUrl: "git+ssh://git@github.com/TracecatHQ/detections.git",
          gitProvider: "github",
          branches: [{ name: "main", is_default: true }],
        })}
        canSync
        canManageConnection
      />
    )

    expect(screen.getByText("Push tab")).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Edit connection" }))
    expect(
      screen.getByRole("heading", { name: "Repository connection" })
    ).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Change" })).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: "Back to changes" }))
    expect(screen.getByText("Push tab")).toBeInTheDocument()
  })
})
