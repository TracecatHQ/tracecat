import { act, render } from "@testing-library/react"
import { useEffect } from "react"
import {
  ApiError,
  type SyncOperationRead,
  workflowsGetSyncOperation,
} from "@/client"
import { QueryClient } from "@/lib/query"
import {
  observeSyncOperation,
  syncOperationPollInterval,
} from "@/lib/workspace-sync-operations"

jest.mock("@/client", () => ({
  ...jest.requireActual("@/client"),
  workflowsGetSyncOperation: jest.fn(),
}))

const operation: SyncOperationRead = {
  id: "operation-example",
  direction: "pull",
  status: "applying",
  stage: "applying",
  created_at: "2026-01-01T00:00:00Z",
  expires_at: "2026-01-02T00:00:00Z",
  can_retry: false,
  inputs: {
    id: "operation-example",
    direction: "pull",
    pull: { commit_sha: "abc" },
  },
}
let client: QueryClient
beforeEach(() => {
  jest.useFakeTimers()
  jest.setSystemTime(new Date("2026-01-01T00:00:00Z"))
  jest.clearAllMocks()
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
})
afterEach(() => {
  client.clear()
  jest.useRealTimers()
})
function Panel() {
  useEffect(() => {
    observeSyncOperation(client, "workspace-example", operation)
  }, [])
  return null
}

it("observes a pull receipt after the panel unmounts, once and in its workspace", async () => {
  client.setQueryData(["workflows", "workspace-example"], ["before"])
  client.setQueryData(["workflows", "another-workspace"], ["unaffected"])
  client.setQueryData(
    ["workflow-definitions", "workspace-example", "workflow-example"],
    ["before"]
  )
  client.setQueryData(
    ["workflow-definitions", "another-workspace", "workflow-example"],
    ["unaffected"]
  )
  client.setQueryData(["sync-operations", "workspace-example", "older"], {
    items: [operation],
    next_cursor: "next",
  })
  const completed = {
    ...operation,
    status: "failed" as const,
    data_applied: true,
    can_retry: true,
  }
  jest.mocked(workflowsGetSyncOperation).mockResolvedValue(completed)
  const invalidate = jest.spyOn(client, "invalidateQueries")
  const view = render(<Panel />)
  expect(workflowsGetSyncOperation).not.toHaveBeenCalled()
  view.unmount()
  await act(async () => {
    await jest.advanceTimersByTimeAsync(1500)
  })
  expect(
    client.getQueryState(["workflows", "workspace-example"])?.isInvalidated
  ).toBe(true)
  expect(
    client.getQueryState(["workflows", "another-workspace"])?.isInvalidated
  ).toBe(false)
  expect(
    client.getQueryData(["sync-operations", "workspace-example", "older"])
  ).toEqual({ items: [completed], next_cursor: "next" })
  expect(
    client.getQueryState([
      "workflow-definitions",
      "workspace-example",
      "workflow-example",
    ])?.isInvalidated
  ).toBe(true)
  expect(
    client.getQueryState([
      "workflow-definitions",
      "another-workspace",
      "workflow-example",
    ])?.isInvalidated
  ).toBe(false)
  const invalidations = invalidate.mock.calls.length
  observeSyncOperation(client, "workspace-example", completed)
  expect(invalidate).toHaveBeenCalledTimes(invalidations)
  await act(async () => {
    await jest.advanceTimersByTimeAsync(6000)
  })
  expect(workflowsGetSyncOperation).toHaveBeenCalledTimes(1)
})

it("expires a ready operation at its deadline even when the refresh fails", async () => {
  const ready = {
    ...operation,
    status: "ready" as const,
    expires_at: new Date(Date.now() + 2000).toISOString(),
  }
  client.setQueryData(["sync-operations", "workspace-example"], {
    items: [ready],
  })
  jest.mocked(workflowsGetSyncOperation).mockRejectedValue(new Error("Offline"))
  observeSyncOperation(client, "workspace-example", ready)
  await act(async () => {
    await jest.advanceTimersByTimeAsync(2000)
  })
  expect(
    client.getQueryData<SyncOperationRead>([
      "sync-operation",
      "workspace-example",
      operation.id,
    ])?.status
  ).toBe("expired")
  expect(
    client.getQueryData<{ items: SyncOperationRead[] }>([
      "sync-operations",
      "workspace-example",
    ])?.items[0].status
  ).toBe("expired")
})

it.each([403, 404])(
  "stops observing permanently rejected operations (%s)",
  async (status) => {
    const error = new ApiError(
      { method: "GET", url: "/sync" },
      { url: "/sync", ok: false, status, statusText: "Rejected", body: null },
      "Rejected"
    )
    jest.mocked(workflowsGetSyncOperation).mockRejectedValue(error)
    observeSyncOperation(client, "workspace-example", operation)
    await act(async () => {
      await jest.advanceTimersByTimeAsync(10_000)
    })
    expect(workflowsGetSyncOperation).toHaveBeenCalledTimes(1)
  }
)

it("stops requests when the query cache is cleared", async () => {
  observeSyncOperation(client, "workspace-example", operation)
  client.clear()
  await act(async () => {
    await jest.advanceTimersByTimeAsync(6000)
  })
  expect(workflowsGetSyncOperation).not.toHaveBeenCalled()
})

it("never replaces newer workflow state with a delayed history row", () => {
  const completed = {
    ...operation,
    status: "completed" as const,
    stage: "finished" as const,
    state_revision: 8,
  }
  client.setQueryData(
    ["sync-operation", "workspace-example", operation.id],
    completed
  )
  observeSyncOperation(client, "workspace-example", {
    ...operation,
    state_revision: 3,
  })
  expect(
    client.getQueryData(["sync-operation", "workspace-example", operation.id])
  ).toEqual(completed)
  expect(workflowsGetSyncOperation).not.toHaveBeenCalled()
})

it("keeps observing a retryable failure until its recorded deadline", () => {
  expect(
    syncOperationPollInterval({
      ...operation,
      status: "failed",
      can_retry: true,
    })
  ).toBe(60_000)
  expect(
    syncOperationPollInterval({
      ...operation,
      status: "failed",
      can_retry: false,
    })
  ).toBe(false)
})

it("refreshes Git caches once when a history-observed push completes", async () => {
  const push = { ...operation, direction: "push" as const }
  for (const key of ["workflow-sync-branches", "repository_commits"]) {
    client.setQueryData([key, "workspace-example", "main"], ["old"])
    client.setQueryData([key, "another-workspace", "main"], ["unaffected"])
  }
  const completed = {
    ...push,
    status: "completed" as const,
    stage: "finished" as const,
  }
  jest.mocked(workflowsGetSyncOperation).mockResolvedValue(completed)
  const invalidate = jest.spyOn(client, "invalidateQueries")
  observeSyncOperation(client, "workspace-example", push)
  await act(async () => {
    await jest.advanceTimersByTimeAsync(1500)
  })
  for (const key of ["workflow-sync-branches", "repository_commits"]) {
    expect(
      client.getQueryState([key, "workspace-example", "main"])?.isInvalidated
    ).toBe(true)
    expect(
      client.getQueryState([key, "another-workspace", "main"])?.isInvalidated
    ).toBe(false)
  }
  expect(invalidate).toHaveBeenCalledTimes(2)
  expect(
    client.getQueryData([
      "workspace-sync-preview-revision",
      "workspace-example",
    ])
  ).toBe(1)
  observeSyncOperation(client, "workspace-example", completed)
  expect(invalidate).toHaveBeenCalledTimes(2)
  expect(
    client.getQueryData([
      "workspace-sync-preview-revision",
      "workspace-example",
    ])
  ).toBe(1)
})

it("expires retryable failures and stops reads at the deadline while offline", async () => {
  const failed = {
    ...operation,
    status: "failed" as const,
    can_retry: true,
    expires_at: new Date(Date.now() + 2000).toISOString(),
  }
  client.setQueryData(["sync-operations", "workspace-example"], {
    items: [failed],
  })
  jest.mocked(workflowsGetSyncOperation).mockRejectedValue(new Error("Offline"))
  observeSyncOperation(client, "workspace-example", failed)
  await act(async () => {
    await jest.advanceTimersByTimeAsync(2000)
  })
  const expired = client.getQueryData<SyncOperationRead>([
    "sync-operation",
    "workspace-example",
    failed.id,
  ])
  expect(expired?.status).toBe("expired")
  expect(expired?.can_retry).toBe(false)
  expect(syncOperationPollInterval(failed)).toBe(false)
  const reads = jest.mocked(workflowsGetSyncOperation).mock.calls.length
  await act(async () => {
    await jest.advanceTimersByTimeAsync(60_000)
  })
  expect(workflowsGetSyncOperation).toHaveBeenCalledTimes(reads)
})

it.each(["push", "pull"] as const)(
  "does not republish an old %s completion after its query is evicted",
  (direction) => {
    const completed: SyncOperationRead = {
      ...operation,
      direction,
      status: "completed",
      stage: "finished",
      data_applied: direction === "pull",
    }
    const revisionKey = ["workspace-sync-preview-revision", "workspace-example"]
    const invalidate = jest.spyOn(client, "invalidateQueries")
    observeSyncOperation(client, "workspace-example", completed)
    const invalidations = invalidate.mock.calls.length
    expect(client.getQueryData(revisionKey)).toBe(1)
    client.removeQueries({
      queryKey: ["sync-operation", "workspace-example", completed.id],
      exact: true,
    })
    observeSyncOperation(client, "workspace-example", completed)
    expect(client.getQueryData(revisionKey)).toBe(1)
    expect(invalidate).toHaveBeenCalledTimes(invalidations)
    // A genuinely new operation must still invalidate the open preview.
    observeSyncOperation(client, "workspace-example", {
      ...completed,
      id: "another-completion",
    })
    expect(client.getQueryData(revisionKey)).toBe(2)
    expect(invalidate.mock.calls.length).toBeGreaterThan(invalidations)
  }
)
