/**
 * @jest-environment jsdom
 */

import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { ReactElement } from "react"
import {
  type GitCommitInfo,
  type PullDiagnostic,
  type PullResult,
  type SyncOperationRead,
  workflowsListSyncDiffs,
} from "@/client"
import { toast } from "@/components/ui/use-toast"
import { GitSyncPullTab } from "@/components/workspace-sync/git-sync-pull"
import { SyncOperationWaitCancelled } from "@/hooks/use-sync-operation"
import { useWorkflowSync } from "@/hooks/use-workspace-sync"
import { QueryClient, QueryClientProvider } from "@/lib/query"

const mockDiff = {
  title: null,
  resource_type: "variable" as const,
  source_id: "example",
  source_path: "variables/example.yml",
  change_type: "modified" as const,
  diff: "",
}

jest.mock("@/client", () => ({
  ...jest.requireActual("@/client"),
  workflowsListSyncDiffs: jest.fn(() => Promise.resolve({ items: [mockDiff] })),
  workflowsGetSyncDiff: jest.fn(() =>
    Promise.resolve({ ...mockDiff, diff: "@@ -1 +1 @@\n-before\n+after" })
  ),
}))

function renderWithClient(ui: ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>)
}

jest.mock("@/hooks/use-workspace-sync", () => ({
  useWorkflowSync: jest.fn(),
}))

jest.mock("@/components/ui/use-toast", () => ({
  toast: jest.fn(),
}))

const mockUseWorkflowSync = useWorkflowSync as jest.MockedFunction<
  typeof useWorkflowSync
>

const COMMIT: GitCommitInfo = {
  sha: "a".repeat(40),
  message: "Update workflows",
  author: "Example author",
  author_email: "author@example.com",
  date: "2026-01-01T00:00:00Z",
}

function pullResult(overrides: Partial<PullResult> = {}): PullResult {
  return {
    success: true,
    commit_sha: COMMIT.sha,
    workflows_found: 1,
    workflows_imported: 0,
    diagnostics: [],
    message: "Dry run completed - 0 resource change(s) detected",
    resource_diffs: [],
    ...overrides,
  }
}

function failedPull(diagnostic: PullDiagnostic, message: string): PullResult {
  return pullResult({ success: false, diagnostics: [diagnostic], message })
}

async function confirmAction(user: {
  click: (element: Element) => Promise<void>
}) {
  const dialog = await screen.findByRole("alertdialog")
  const action = within(dialog)
    .getAllByRole("button")
    .find((button) => button.textContent !== "Cancel")
  if (!action) throw new Error("Confirmation dialog has no action")
  await user.click(action)
}

function renderPullTab(
  pullWorkflows: jest.Mock,
  operation: Pick<SyncOperationRead, "id" | "status" | "diff_count"> = {
    id: "pull-operation",
    status: "ready",
    diff_count: 0,
  }
) {
  mockUseWorkflowSync.mockReturnValue({
    pullWorkflows,
    pullWorkflowsIsPending: false,
    pullWorkflowsError: null,
    previewOperation: operation,
  } as unknown as ReturnType<typeof useWorkflowSync>)
  renderWithClient(
    <GitSyncPullTab
      workspaceId="workspace-1"
      workspaceName="Example workspace"
      provider="github"
      baseBranch="main"
      commits={[COMMIT]}
      commitsIsLoading={false}
      commitsError={null}
      isActive
      onPulled={jest.fn()}
    />
  )
}

describe("GitSyncPullTab failures", () => {
  it("shows the diagnostic that names the workflow when a preview fails", async () => {
    const message =
      "Workflow 'Example enrich' uses action 'tools.example.removed', which is in no registry"
    renderPullTab(
      jest.fn().mockResolvedValue(
        failedPull(
          {
            workflow_path: "workflows/wf_example/definition.yml",
            workflow_title: "Example enrich",
            error_type: "dependency",
            message,
            details: {},
          },
          "Failed to validate workspace spec: 1 issue(s)"
        )
      )
    )

    await userEvent.click(screen.getByRole("button", { name: "Preview" }))

    expect(await screen.findByText(message)).toBeInTheDocument()
    expect(screen.getByText("Example enrich")).toBeInTheDocument()
  })

  it("shows the diagnostic that names the workflow when an apply fails", async () => {
    const message =
      "Failed to import workflow 'Example enrich': Action 'tools.example.removed' not found in any registry."
    renderPullTab(
      jest
        .fn()
        .mockResolvedValueOnce(pullResult())
        .mockResolvedValueOnce(
          failedPull(
            {
              workflow_path: "workflows/example-enrich/definition.yml",
              workflow_title: "Example enrich",
              error_type: "transaction",
              message,
              details: {},
            },
            "Workspace import failed on workflow 'Example enrich'"
          )
        )
    )

    await userEvent.click(screen.getByRole("button", { name: "Preview" }))
    await userEvent.click(
      await screen.findByRole("button", { name: "Pull into Example workspace" })
    )
    await confirmAction(userEvent)

    expect(await screen.findByText(message)).toBeInTheDocument()
    expect(screen.getByText("Example enrich")).toBeInTheDocument()
    expect(
      screen.getByText("Workspace import failed on workflow 'Example enrich'")
    ).toBeInTheDocument()
  })
})

it("does not toast a failed preview when its local waiter detaches", async () => {
  jest.mocked(toast).mockClear()
  const pull = jest.fn().mockRejectedValueOnce(new SyncOperationWaitCancelled())
  renderPullTab(pull)
  await userEvent.click(screen.getByRole("button", { name: "Preview" }))
  expect(pull).toHaveBeenCalledTimes(1)
  expect(toast).not.toHaveBeenCalled()
})

it.each([0, 1])(
  "shows computed durable diffs on a failed pull preview (count=%s)",
  async (count) => {
    jest.mocked(workflowsListSyncDiffs).mockClear()
    renderPullTab(
      jest.fn().mockResolvedValue(
        failedPull(
          {
            workflow_path: "workflows/synthetic/definition.yml",
            workflow_title: "Synthetic",
            error_type: "validation",
            message: "Synthetic validation failure",
            details: {},
          },
          "Preview validation failed"
        )
      ),
      { id: "failed-pull", status: "failed", diff_count: count }
    )
    await userEvent.click(screen.getByRole("button", { name: "Preview" }))
    expect(
      await screen.findByText("Synthetic validation failure")
    ).toBeVisible()
    if (count > 0)
      expect(workflowsListSyncDiffs).toHaveBeenCalledWith({
        workspaceId: "workspace-1",
        operationId: "failed-pull",
        cursor: undefined,
      })
    else {
      expect(workflowsListSyncDiffs).not.toHaveBeenCalled()
      expect(screen.queryByText("Already matches")).not.toBeInTheDocument()
    }
    expect(
      screen.getByRole("button", { name: /into Example workspace/ })
    ).toBeDisabled()
  }
)
