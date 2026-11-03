import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"
import {
  type SyncOperationRead,
  workflowsGetSyncOperation,
  workflowsListSyncOperations,
  workflowsRetrySyncOperation,
} from "@/client"
import { SyncOperationStatus } from "@/components/workspace-sync/sync-operation-status"
import { QueryClient, QueryClientProvider } from "@/lib/query"

jest.mock("@/client", () => ({
  ...jest.requireActual("@/client"),
  workflowsGetSyncOperation: jest.fn(),
  workflowsListSyncOperations: jest.fn(),
  workflowsRetrySyncOperation: jest.fn(),
}))

const operation: SyncOperationRead = {
  id: "operation-test",
  direction: "push",
  status: "ready",
  stage: "awaiting_confirmation",
  created_at: "2026-01-01T00:00:00Z",
  expires_at: "2099-01-02T00:00:00Z",
  diff_count: 51,
  can_retry: false,
  preview_revision: 1,
  state_revision: 3,
  inputs: {
    id: "operation-test",
    direction: "push",
    push: { branch: "sync/test", message: "Sync test resources" },
  },
}
function mountStatus(existingClient?: QueryClient) {
  const client =
    existingClient ??
    new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 } },
    })
  // Model the connected view's subscription to this local import counter.
  client.setQueryDefaults(["workspace-sync-preview-revision"], {
    gcTime: Infinity,
  })
  return {
    ...render(
      <QueryClientProvider client={client}>
        <SyncOperationStatus workspaceId="workspace-test" />
      </QueryClientProvider>
    ),
    client,
  }
}

beforeEach(() => {
  jest.clearAllMocks()
  jest.mocked(workflowsGetSyncOperation).mockResolvedValue(operation)
  jest
    .mocked(workflowsListSyncOperations)
    .mockResolvedValue({ items: [operation] })
})

it.each([
  ["ready", "awaiting_confirmation"],
  ["expired", "awaiting_confirmation"],
  ["completed", "finished"],
] as const)(
  "shows nothing when the latest operation is %s",
  async (status, stage) => {
    jest.mocked(workflowsListSyncOperations).mockResolvedValue({
      items: [{ ...operation, status, stage }],
    })
    const view = mountStatus()
    await waitFor(() => expect(workflowsListSyncOperations).toHaveBeenCalled())
    await act(async () => {
      await Promise.resolve()
    })
    expect(view.container).toBeEmptyDOMElement()
    view.unmount()
  }
)

it("says a push is applying in the background", async () => {
  jest.mocked(workflowsListSyncOperations).mockResolvedValue({
    items: [{ ...operation, status: "applying", stage: "applying" }],
  })
  const view = mountStatus()
  expect(
    await screen.findByText(
      "Pushing to sync/test in the background. You can leave this page."
    )
  ).toBeInTheDocument()
  view.unmount()
})

it("shows the failure reason and retries a failed pull", async () => {
  const failed: SyncOperationRead = {
    ...operation,
    direction: "pull",
    status: "failed",
    stage: "applying",
    data_applied: true,
    can_retry: true,
    diff_count: 0,
    error: "Sync failed at this stage. Retry to resume the operation.",
    inputs: {
      id: operation.id,
      direction: "pull",
      pull: { commit_sha: "a".repeat(40) },
    },
  }
  jest
    .mocked(workflowsListSyncOperations)
    .mockResolvedValue({ items: [failed] })
  jest.mocked(workflowsRetrySyncOperation).mockResolvedValue({
    ...failed,
    status: "applying",
    can_retry: false,
  })
  const view = mountStatus()
  const notice = await screen.findByRole("alert")
  expect(notice).toHaveTextContent("Last pull did not finish.")
  expect(notice).toHaveTextContent(
    "Workspace changes were already imported and will not be imported again."
  )
  fireEvent.click(screen.getByRole("button", { name: "Retry" }))
  await waitFor(() =>
    expect(workflowsRetrySyncOperation).toHaveBeenCalledWith(
      expect.objectContaining({
        workspaceId: "workspace-test",
        operationId: operation.id,
      })
    )
  )
  view.unmount()
})

it("stops polling history when every operation is terminal", async () => {
  jest.useFakeTimers()
  try {
    jest.mocked(workflowsListSyncOperations).mockResolvedValue({
      items: [
        { ...operation, status: "completed", stage: "finished", diff_count: 0 },
      ],
    })
    const view = mountStatus()
    await act(async () => {
      await jest.advanceTimersByTimeAsync(0)
    })
    const calls = jest.mocked(workflowsListSyncOperations).mock.calls.length
    expect(calls).toBeGreaterThan(0)
    await act(async () => {
      jest.advanceTimersByTime(6000)
    })
    expect(workflowsListSyncOperations).toHaveBeenCalledTimes(calls)
    view.unmount()
  } finally {
    jest.useRealTimers()
  }
})

it("keeps observing an active operation and notifies composers when it imports", async () => {
  jest.useFakeTimers()
  try {
    const active = {
      ...operation,
      direction: "pull" as const,
      diff_count: 0,
      status: "running" as const,
      stage: "preparing" as const,
    }
    jest
      .mocked(workflowsListSyncOperations)
      .mockResolvedValue({ items: [active] })
    jest.mocked(workflowsGetSyncOperation).mockResolvedValue({
      ...active,
      status: "applying",
      stage: "finishing",
      data_applied: true,
    })
    const view = mountStatus()
    await act(async () => {
      await jest.advanceTimersByTimeAsync(0)
    })
    const previousRevision =
      view.client.getQueryData<number>([
        "workspace-sync-preview-revision",
        "workspace-test",
      ]) ?? 0
    await act(async () => {
      await jest.advanceTimersByTimeAsync(1500)
    })
    expect(workflowsGetSyncOperation).toHaveBeenCalledWith({
      workspaceId: "workspace-test",
      operationId: operation.id,
    })
    expect(
      view.client.getQueryData([
        "workspace-sync-preview-revision",
        "workspace-test",
      ])
    ).toBe(previousRevision + 1)
    view.unmount()
  } finally {
    jest.useRealTimers()
  }
})

it.each([
  ["push", false],
  ["pull", false],
  ["push", true],
  ["pull", true],
] as const)(
  "announces a new remote %s completion after an empty baseline (remount=%s)",
  async (direction, remount) => {
    jest.mocked(workflowsListSyncOperations).mockResolvedValue({ items: [] })
    let view = mountStatus()
    const { client } = view
    await waitFor(() =>
      expect(
        client.getQueryData(["sync-operations", "workspace-test", undefined])
      ).toEqual({ items: [] })
    )
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0))
    })
    const revisionKey = ["workspace-sync-preview-revision", "workspace-test"]
    client.setQueryData(revisionKey, 5)
    const remote = {
      ...operation,
      id: "completed-in-another-tab",
      direction,
      status: "completed" as const,
      stage: "finished" as const,
      diff_count: 0,
      data_applied: direction === "pull",
    }
    jest
      .mocked(workflowsListSyncOperations)
      .mockResolvedValue({ items: [remote] })
    if (remount) {
      view.unmount()
      client.removeQueries({ queryKey: ["sync-operations", "workspace-test"] })
      view = mountStatus(client)
    } else {
      await act(async () => {
        await client.invalidateQueries({
          queryKey: ["sync-operations", "workspace-test"],
        })
      })
    }
    await waitFor(() => expect(client.getQueryData(revisionKey)).toBe(6))
    await act(async () => {
      await client.invalidateQueries({
        queryKey: ["sync-operations", "workspace-test"],
      })
    })
    expect(client.getQueryData(revisionKey)).toBe(6)
    view.unmount()
    client.clear()
  }
)
