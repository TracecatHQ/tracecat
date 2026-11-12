/**
 * @jest-environment jsdom
 */

import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { settingsUpdateGitSettings } from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { OrgRegistryRepositoryRemoveSection } from "@/components/organization/org-registry-repository-remove"
import { TooltipProvider } from "@/components/ui/tooltip"
import { useOrgGitSettings, useRegistryRepositories } from "@/lib/hooks"
import { useQueryClient } from "@/lib/query"

jest.mock("@/client", () => ({
  ...jest.requireActual("@/client"),
  settingsUpdateGitSettings: jest.fn(),
}))

jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: jest.fn(),
}))

jest.mock("@/components/ui/use-toast", () => ({
  toast: jest.fn(),
}))

jest.mock("@/lib/hooks", () => ({
  useOrgGitSettings: jest.fn(),
  useRegistryRepositories: jest.fn(),
}))

jest.mock("@/lib/query", () => ({
  useQueryClient: jest.fn(),
}))

const CONNECTED_ORIGIN = "git+ssh://git@github.com/example/connected.git"
const ORPHANED_ORIGIN = "git+ssh://git@github.com/example/orphaned.git"

const mockUseScopeCheck = useScopeCheck as jest.MockedFunction<
  typeof useScopeCheck
>
const mockUseOrgGitSettings = useOrgGitSettings as jest.MockedFunction<
  typeof useOrgGitSettings
>
const mockUseRegistryRepositories =
  useRegistryRepositories as jest.MockedFunction<typeof useRegistryRepositories>
const mockUseQueryClient = useQueryClient as jest.MockedFunction<
  typeof useQueryClient
>
const mockSettingsUpdateGitSettings =
  settingsUpdateGitSettings as jest.MockedFunction<
    typeof settingsUpdateGitSettings
  >

function repo(id: string, origin: string) {
  return {
    id,
    origin,
    last_synced_at: null,
    commit_sha: null,
    current_version_id: null,
  }
}

describe("OrgRegistryRepositoryRemoveSection", () => {
  const deleteRepo = jest.fn()
  const invalidateQueries = jest.fn()

  beforeEach(() => {
    jest.clearAllMocks()
    mockUseScopeCheck.mockReturnValue(true)
    mockUseQueryClient.mockReturnValue({
      invalidateQueries,
    } as unknown as ReturnType<typeof useQueryClient>)
    mockSettingsUpdateGitSettings.mockResolvedValue(undefined as never)
    deleteRepo.mockResolvedValue(undefined)
    mockUseOrgGitSettings.mockReturnValue({
      gitSettings: {
        git_allowed_domains: ["github.com"],
        git_repo_url: CONNECTED_ORIGIN,
        git_repo_package_name: null,
      },
    } as unknown as ReturnType<typeof useOrgGitSettings>)
    mockUseRegistryRepositories.mockReturnValue({
      repos: [
        repo("repo-connected", CONNECTED_ORIGIN),
        repo("repo-orphaned", ORPHANED_ORIGIN),
        repo("repo-local", "local"),
      ],
      deleteRepo,
      deleteRepoIsPending: false,
    } as unknown as ReturnType<typeof useRegistryRepositories>)
  })

  function renderSection() {
    render(
      <TooltipProvider>
        <OrgRegistryRepositoryRemoveSection />
      </TooltipProvider>
    )
  }

  async function openRemoveDialog(origin: string) {
    const user = userEvent.setup()
    renderSection()
    const row = screen.getByText(origin).closest("li")
    if (!row) {
      throw new Error(`No row for ${origin}`)
    }
    await user.click(
      Array.from(row.querySelectorAll("button")).find(
        (button) => button.textContent === "Remove"
      ) as HTMLButtonElement
    )
    return user
  }

  async function removeRepository(origin: string) {
    const user = await openRemoveDialog(origin)
    await user.type(
      screen.getByLabelText("Type the repository URL to confirm"),
      origin
    )
    await user.click(screen.getByRole("button", { name: "Remove" }))
  }

  it("keeps Remove disabled until the repository URL is typed", async () => {
    const user = await openRemoveDialog(ORPHANED_ORIGIN)
    const confirm = screen.getByRole("button", { name: "Remove" })
    expect(confirm).toBeDisabled()

    await user.type(
      screen.getByLabelText("Type the repository URL to confirm"),
      CONNECTED_ORIGIN
    )
    expect(confirm).toBeDisabled()
    expect(deleteRepo).not.toHaveBeenCalled()
  })

  it("clears the repository URL before deleting the connected repository", async () => {
    await removeRepository(CONNECTED_ORIGIN)

    await waitFor(() =>
      expect(deleteRepo).toHaveBeenCalledWith({
        repositoryId: "repo-connected",
      })
    )
    expect(mockSettingsUpdateGitSettings).toHaveBeenCalledWith({
      requestBody: { git_repo_url: null, git_repo_package_name: null },
    })
    expect(
      mockSettingsUpdateGitSettings.mock.invocationCallOrder[0]
    ).toBeLessThan(deleteRepo.mock.invocationCallOrder[0])
  })

  it("deletes a repository that is no longer connected without touching settings", async () => {
    await removeRepository(ORPHANED_ORIGIN)

    await waitFor(() =>
      expect(deleteRepo).toHaveBeenCalledWith({
        repositoryId: "repo-orphaned",
      })
    )
    expect(mockSettingsUpdateGitSettings).not.toHaveBeenCalled()
  })

  it("does not delete the repository when clearing the URL fails", async () => {
    mockSettingsUpdateGitSettings.mockRejectedValue(new Error("forbidden"))

    await removeRepository(CONNECTED_ORIGIN)

    await waitFor(() =>
      expect(mockSettingsUpdateGitSettings).toHaveBeenCalled()
    )
    expect(deleteRepo).not.toHaveBeenCalled()
  })

  it("lists only custom Git repositories", () => {
    renderSection()

    expect(screen.getByText(CONNECTED_ORIGIN)).toBeTruthy()
    expect(screen.getByText(ORPHANED_ORIGIN)).toBeTruthy()
    expect(screen.queryByText("local")).toBeNull()
  })
})
