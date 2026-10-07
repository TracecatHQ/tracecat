/** @jest-environment jsdom */

import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { useState } from "react"
import type { WorkspaceSyncExportPreview } from "@/client"
import { WorkspaceResourceSyncActions } from "@/components/workspace-sync/resource-sync-actions"
import { useWorkspaceSyncExportPreview } from "@/hooks/use-workspace-sync"

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
    render(
      <WorkspaceResourceSyncActions
        label="Variables"
        branchSlug="variables"
        resources={["variable"]}
      />
    )
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
