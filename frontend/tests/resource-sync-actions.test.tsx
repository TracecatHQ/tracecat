/** @jest-environment jsdom */

import { act, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { useState } from "react"
import type { SyncOperationRead, WorkspaceSyncExportPreview } from "@/client"
import { toast } from "@/components/ui/use-toast"
import { WorkspaceResourceSyncActions } from "@/components/workspace-sync/resource-sync-actions"
import { SyncOperationWaitCancelled } from "@/hooks/use-sync-operation"
import { useWorkspaceSyncExportPreview } from "@/hooks/use-workspace-sync"

import { QueryClient, QueryClientProvider } from "@/lib/query"
import { observeSyncOperation } from "@/lib/workspace-sync-operations"

let client: QueryClient
function renderActions() {
  return render(
    <QueryClientProvider client={client}>
      <WorkspaceResourceSyncActions
        label="Variables"
        branchSlug="variables"
        resources={["variable"]}
      />
    </QueryClientProvider>
  )
}

const mockPreviewRequests = jest.fn()
const mockExport = jest.fn()
const preview: WorkspaceSyncExportPreview = {
  files: [],
  resources: [],
  resource_counts: {},
  resource_diffs: [],
}

jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: () => true,
}))
jest.mock("@/hooks/use-entitlements", () => ({
  useEntitlements: () => ({ hasEntitlement: () => true, isLoading: false }),
}))
jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: () => "workspace-test",
}))
jest.mock("@/hooks/use-workspace", () => ({
  useWorkspaceDetails: () => ({
    workspace: {
      settings: {
        git_repo_url: "git+ssh://git@github.com/example/sync.git",
        git_provider: "github",
      },
    },
  }),
}))
jest.mock("@/components/ui/use-toast", () => ({ toast: jest.fn() }))
jest.mock("@/components/workspace-sync/sync-operation-diffs", () => ({
  OperationDiffs: () => null,
}))
jest.mock("@/hooks/use-workspace-sync", () => ({
  useRepositoryBranches: () => ({
    branches: [{ name: "main", is_default: true }],
    branchesIsLoading: false,
  }),
  useWorkspaceSyncExport: () => ({
    exportWorkspace: mockExport,
    exportWorkspaceIsPending: false,
  }),
  useWorkspaceSyncExportPreview: jest.fn(),
}))

beforeEach(() => {
  jest.clearAllMocks()
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  jest
    .mocked(useWorkspaceSyncExportPreview)
    .mockImplementation(function usePreview(_workspaceId, options) {
      const key = JSON.stringify(options?.push)
      const [preparedKey, setPreparedKey] = useState<string>()
      return {
        preview: preparedKey === key ? preview : undefined,
        previewOperationId: preparedKey === key ? "operation-test" : undefined,
        previewDiffCount: 0,
        previewIsLoading: false,
        previewError: null,
        refetchPreview: () => {
          mockPreviewRequests(options?.push)
          setPreparedKey(key)
        },
      }
    })
})

it.each(["message", "branch"])(
  "allows a fresh resource preview after editing its %s",
  async (field) => {
    const user = userEvent.setup()
    renderActions()
    await user.click(screen.getByRole("button", { name: "Push" }))
    await user.click(screen.getByRole("button", { name: "Preview changes" }))
    expect(
      screen.getByRole("button", { name: "Refresh preview" })
    ).toBeVisible()
    const message =
      field === "message"
        ? screen.getByRole("textbox", { name: "Commit message" })
        : screen.getByPlaceholderText("sync/variables")
    await user.clear(message)
    await user.type(message, "updated-resource")
    expect(
      screen.getByRole("button", { name: "Preview changes" })
    ).toBeEnabled()
    await user.click(screen.getByRole("button", { name: "Preview changes" }))
    expect(
      screen.getByRole("button", { name: "Refresh preview" })
    ).toBeVisible()
    expect(mockPreviewRequests).toHaveBeenLastCalledWith(
      expect.objectContaining({ [field]: "updated-resource" })
    )
    expect(mockExport).not.toHaveBeenCalled()
  }
)

it("disables confirmation while a replacement preview is pending", async () => {
  jest
    .mocked(useWorkspaceSyncExportPreview)
    .mockImplementation(function usePreview() {
      const [requests, setRequests] = useState(0)
      return {
        preview,
        previewOperationId: "previous-operation",
        previewDiffCount: 0,
        previewIsLoading: requests > 1,
        previewError: null,
        refetchPreview: () => setRequests((value) => value + 1),
      }
    })
  const user = userEvent.setup()
  renderActions()
  await user.click(screen.getByRole("button", { name: "Push" }))
  expect(screen.getByRole("button", { name: "Push & open PR" })).toBeDisabled()
  await user.click(screen.getByRole("button", { name: "Preview changes" }))
  expect(screen.getByRole("button", { name: "Push & open PR" })).toBeEnabled()
  await user.click(screen.getByRole("button", { name: "Refresh preview" }))
  const push = screen.getByRole("button", { name: "Push & open PR" })
  expect(push).toBeDisabled()
  await user.click(push)
  expect(mockExport).not.toHaveBeenCalled()
})

afterEach(() => client.clear())

it.each(["push", "pull"] as const)(
  "clears a reviewed resource preview after another %s finishes, preserving drafts",
  async (direction) => {
    const user = userEvent.setup()
    renderActions()
    await user.click(screen.getByRole("button", { name: "Push" }))
    const message = screen.getByRole("textbox", { name: "Commit message" })
    await user.clear(message)
    await user.type(message, "Synthetic reviewed message")
    const branch = screen.getByPlaceholderText("sync/variables")
    await user.clear(branch)
    await user.type(branch, "sync/synthetic")
    await user.click(screen.getByRole("button", { name: "Preview changes" }))
    expect(screen.getByRole("button", { name: "Push & open PR" })).toBeEnabled()
    const completed: SyncOperationRead = {
      id: "other-operation",
      direction,
      status: "completed",
      stage: "finished",
      created_at: new Date().toISOString(),
      expires_at: new Date(Date.now() + 86400000).toISOString(),
      can_retry: false,
      data_applied: direction === "pull",
      inputs: {
        id: "other-operation",
        direction,
        ...(direction === "push"
          ? { push: { branch: "sync/synthetic", message: "Synthetic sync" } }
          : { pull: { commit_sha: "abc" } }),
      },
    }
    act(() => observeSyncOperation(client, "workspace-test", completed))
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Push & open PR" })
      ).toBeDisabled()
    )
    expect(message).toHaveValue("Synthetic reviewed message")
    expect(branch).toHaveValue("sync/synthetic")
    await user.click(screen.getByRole("button", { name: "Preview changes" }))
    expect(screen.getByRole("button", { name: "Push & open PR" })).toBeEnabled()
    // Observing the same receipt twice must not clear the fresh preview again.
    act(() => observeSyncOperation(client, "workspace-test", completed))
    expect(screen.getByRole("button", { name: "Push & open PR" })).toBeEnabled()
    expect(mockExport).not.toHaveBeenCalled()
  }
)

it("does not report a background continuation as a failed resource push", async () => {
  const user = userEvent.setup()
  mockExport.mockRejectedValueOnce(new SyncOperationWaitCancelled())
  renderActions()
  await user.click(screen.getByRole("button", { name: "Push" }))
  await user.click(screen.getByRole("button", { name: "Preview changes" }))
  await user.click(screen.getByRole("button", { name: "Push & open PR" }))
  await waitFor(() => expect(mockExport).toHaveBeenCalledTimes(1))
  expect(toast).not.toHaveBeenCalled()
})
