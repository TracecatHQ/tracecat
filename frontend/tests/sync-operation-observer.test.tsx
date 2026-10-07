import { act, render } from "@testing-library/react"
import { useEffect } from "react"
import {
  ApiError,
  type SyncOperationRead,
  workflowsGetSyncOperation,
} from "@/client"
import { QueryClient } from "@/lib/query"
import { observeSyncOperation } from "@/lib/workspace-sync-operations"

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
