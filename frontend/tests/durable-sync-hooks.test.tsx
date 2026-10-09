import { act, renderHook, waitFor } from "@testing-library/react"
import type { ReactNode } from "react"
import {
  ApiError,
  CancelablePromise,
  type SyncOperationRead,
  workflowsApplySyncOperation,
  workflowsCreateSyncOperation,
  workflowsGetSyncOperation,
} from "@/client"
import {
  SyncOperationPollingError,
  useSyncOperation,
  useSyncOperationWaiter,
} from "@/hooks/use-sync-operation"
import {
  useWorkflowSync,
  useWorkspaceSyncExportPreview,
} from "@/hooks/use-workspace-sync"
import { QueryClient, QueryClientProvider } from "@/lib/query"

jest.mock("@/client", () => ({
  ...jest.requireActual("@/client"),
  workflowsCreateSyncOperation: jest.fn(),
  workflowsGetSyncOperation: jest.fn(),
  workflowsApplySyncOperation: jest.fn(),
}))

const operation: SyncOperationRead = {
  id: "operation-test",
  direction: "pull",
  status: "ready",
  stage: "awaiting_confirmation",
  created_at: "2026-01-01T00:00:00Z",
  expires_at: "2099-01-02T00:00:00Z",
  diff_count: 0,
  can_retry: false,
  inputs: {
    id: "operation-test",
    direction: "pull",
    pull: { commit_sha: "abc" },
  },
  preview: {
    success: true,
    commit_sha: "abc",
    diagnostics: [],
    message: "Preview ready",
    workflows_found: 0,
    workflows_imported: 0,
  },
}

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

beforeEach(() => {
  jest.resetAllMocks()
  jest.mocked(workflowsCreateSyncOperation).mockResolvedValue(operation)
  jest.mocked(workflowsGetSyncOperation).mockResolvedValue(operation)
})

afterEach(() => jest.useRealTimers())

it("previews without the legacy dry-run flag and applies only after confirmation", async () => {
  const { result } = renderHook(() => useWorkflowSync("workspace-test"), {
    wrapper,
  })
  await act(async () => {
    await result.current.pullWorkflows({ commit_sha: "abc", dry_run: true })
  })
  expect(workflowsCreateSyncOperation).toHaveBeenCalledWith(
    expect.objectContaining({
      requestBody: expect.objectContaining({
        pull: { commit_sha: "abc", sync_schedules: false },
      }),
    })
  )
  expect(workflowsApplySyncOperation).not.toHaveBeenCalled()
  const completed: SyncOperationRead = {
    ...operation,
    status: "completed",
    stage: "finished",
    result: {
      success: true,
      commit_sha: "abc",
      workflows_found: 0,
      workflows_imported: 0,
      diagnostics: [],
      message: "Completed",
    },
  }
  jest.mocked(workflowsApplySyncOperation).mockResolvedValue(completed)
  jest.mocked(workflowsGetSyncOperation).mockResolvedValue(completed)
  await act(async () => {
    await result.current.pullWorkflows({ commit_sha: "abc", dry_run: false })
  })
  expect(workflowsApplySyncOperation).toHaveBeenCalledWith({
    workspaceId: "workspace-test",
    operationId: operation.id,
  })
  expect(workflowsCreateSyncOperation).toHaveBeenCalledTimes(1)
})

it("retries transient status failures for the same accepted operation", async () => {
  jest.useFakeTimers()
  jest
    .mocked(workflowsGetSyncOperation)
    .mockRejectedValueOnce(new TypeError("Network unavailable"))
  const { result } = renderHook(() => useSyncOperationWaiter("workspace-test"))
  const waiting = result.current(operation.id)
  await act(async () => {
    await jest.advanceTimersByTimeAsync(1000)
  })
  await expect(waiting).resolves.toEqual(operation)
  expect(workflowsGetSyncOperation).toHaveBeenCalledTimes(2)
  expect(workflowsGetSyncOperation).toHaveBeenLastCalledWith({
    workspaceId: "workspace-test",
    operationId: operation.id,
  })
})

it("bounds repeated status failures without declaring the job failed", async () => {
  jest.useFakeTimers()
  jest
    .mocked(workflowsGetSyncOperation)
    .mockRejectedValue(new TypeError("Network unavailable"))
  const { result } = renderHook(() => useSyncOperationWaiter("workspace-test"))
  const waiting = result.current(operation.id).catch((error: unknown) => error)
  await act(async () => {
    await jest.advanceTimersByTimeAsync(7000)
  })
  expect(await waiting).toBeInstanceOf(SyncOperationPollingError)
  expect(workflowsGetSyncOperation).toHaveBeenCalledTimes(4)
})

it("does not retry a forbidden status request", async () => {
  const error = new ApiError(
    { method: "GET", url: "/sync" },
    {
      url: "/sync",
      ok: false,
      status: 403,
      statusText: "Forbidden",
      body: null,
    },
    "Forbidden"
  )
  jest.mocked(workflowsGetSyncOperation).mockRejectedValue(error)
  const { result } = renderHook(() => useSyncOperationWaiter("workspace-test"))
  await expect(result.current(operation.id)).rejects.toBe(error)
  expect(workflowsGetSyncOperation).toHaveBeenCalledTimes(1)
})

it("preserves the pull request ID after an ambiguous creation response", async () => {
  jest
    .mocked(workflowsCreateSyncOperation)
    .mockRejectedValueOnce(new TypeError("Response lost"))
  const { result } = renderHook(() => useWorkflowSync("workspace-test"), {
    wrapper,
  })
  await act(async () => {
    await expect(
      result.current.pullWorkflows({ commit_sha: "abc", dry_run: true })
    ).rejects.toThrow("Response lost")
  })
  await act(async () => {
    await result.current.pullWorkflows({ commit_sha: "abc", dry_run: true })
  })
  const calls = jest.mocked(workflowsCreateSyncOperation).mock.calls
  expect(calls[1][0]?.requestBody.id).toBe(calls[0][0]?.requestBody.id)
})

it("shows a push polling error and retries GET without creating another job", async () => {
  jest.useFakeTimers()
  jest
    .mocked(workflowsCreateSyncOperation)
    .mockResolvedValue({ ...operation, status: "queued" })
  jest
    .mocked(workflowsGetSyncOperation)
    .mockRejectedValue(new TypeError("Response lost"))
  const { result } = renderHook(
    () =>
      useWorkspaceSyncExportPreview("workspace-test", {
        push: { branch: "sync/test", message: "Sync" },
      }),
    { wrapper }
  )
  act(() => result.current.refetchPreview())
  await waitFor(() => expect(workflowsGetSyncOperation).toHaveBeenCalled())
  await act(async () => {
    await jest.advanceTimersByTimeAsync(7100)
  })
  expect(result.current.previewError).toBeTruthy()
  expect(result.current.previewIsLoading).toBe(false)
  jest.mocked(workflowsGetSyncOperation).mockResolvedValue(operation)
  act(() => result.current.refetchPreview())
  await waitFor(() =>
    expect(result.current.previewOperationId).toBe(operation.id)
  )
  expect(workflowsCreateSyncOperation).toHaveBeenCalledTimes(1)
})

it("removes the previous prepared ID while refreshing and after a failed refresh", async () => {
  const { result } = renderHook(
    () =>
      useWorkspaceSyncExportPreview("workspace-test", {
        push: { branch: "sync/test", message: "Sync" },
      }),
    { wrapper }
  )
  act(() => result.current.refetchPreview())
  await waitFor(() =>
    expect(result.current.previewOperationId).toBe(operation.id)
  )
  let rejectRefresh!: (reason: Error) => void
  jest.mocked(workflowsCreateSyncOperation).mockImplementationOnce(
    () =>
      new CancelablePromise((_resolve, reject) => {
        rejectRefresh = reject
      })
  )
  act(() => result.current.refetchPreview())
  await waitFor(() => expect(result.current.previewIsLoading).toBe(true))
  expect(result.current.previewOperationId).toBeUndefined()
  await act(async () => rejectRefresh(new Error("Refresh failed")))
  await waitFor(() => expect(result.current.previewError).toBeTruthy())
  expect(result.current.previewOperationId).toBeUndefined()
})

it("starts a fresh user-requested preview after a permanent missing-operation error", async () => {
  jest
    .mocked(workflowsCreateSyncOperation)
    .mockResolvedValue({ ...operation, status: "queued" })
  jest.mocked(workflowsGetSyncOperation).mockRejectedValue(
    new ApiError(
      { method: "GET", url: "/sync" },
      {
        url: "/sync",
        ok: false,
        status: 404,
        statusText: "Not found",
        body: null,
      },
      "Not found"
    )
  )
  const { result } = renderHook(
    () =>
      useWorkspaceSyncExportPreview("workspace-test", {
        push: { branch: "sync/test", message: "Sync" },
      }),
    { wrapper }
  )
  act(() => result.current.refetchPreview())
  await waitFor(() => expect(result.current.previewError).toBeTruthy())
  expect(workflowsCreateSyncOperation).toHaveBeenCalledTimes(1)
  jest
    .mocked(workflowsCreateSyncOperation)
    .mockResolvedValue({ ...operation, id: "fresh-operation" })
  jest
    .mocked(workflowsGetSyncOperation)
    .mockResolvedValue({ ...operation, id: "fresh-operation" })
  act(() => result.current.refetchPreview())
  await waitFor(() =>
    expect(result.current.previewOperationId).toBe("fresh-operation")
  )
  const calls = jest.mocked(workflowsCreateSyncOperation).mock.calls
  expect(calls[1][0].requestBody.id).not.toBe(calls[0][0].requestBody.id)
})

it.each([403, 404])(
  "stops polling a permanent error without cached data (%s)",
  async (status) => {
    jest.useFakeTimers()
    jest.mocked(workflowsGetSyncOperation).mockRejectedValue(
      new ApiError(
        { method: "GET", url: "/sync" },
        {
          url: "/sync",
          ok: false,
          status,
          statusText: "Rejected",
          body: null,
        },
        "Rejected"
      )
    )
    const view = renderHook(
      () => useSyncOperation("workspace-test", operation.id),
      { wrapper }
    )
    await act(async () => {
      await jest.advanceTimersByTimeAsync(10_000)
    })
    expect(workflowsGetSyncOperation).toHaveBeenCalledTimes(1)
    view.unmount()
  }
)

it.each([true, false])(
  "rejects an expired pull before POSTing (observed expiry: %s)",
  async (observeExpiry) => {
    jest.useFakeTimers()
    const ready = {
      ...operation,
      expires_at: new Date(Date.now() + 2000).toISOString(),
    }
    jest.mocked(workflowsCreateSyncOperation).mockResolvedValue(ready)
    jest.mocked(workflowsGetSyncOperation).mockResolvedValue(ready)
    const { result } = renderHook(() => useWorkflowSync("workspace-test"), {
      wrapper,
    })
    await act(async () => {
      await result.current.pullWorkflows({ commit_sha: "abc", dry_run: true })
    })
    await waitFor(() =>
      expect(result.current.previewOperation?.status).toBe("ready")
    )
    if (observeExpiry) {
      await act(async () => {
        await jest.advanceTimersByTimeAsync(2100)
      })
      expect(result.current.previewOperation?.status).toBe("expired")
    } else {
      // A click can arrive before the observer's timer has delivered its update.
      jest.setSystemTime(Date.now() + 2001)
    }
    await act(async () => {
      await expect(
        result.current.pullWorkflows({ commit_sha: "abc" })
      ).rejects.toThrow("Preview expired or is no longer ready")
    })
    expect(workflowsApplySyncOperation).not.toHaveBeenCalled()
    const fresh = {
      ...operation,
      id: "fresh-operation",
      expires_at: new Date(Date.now() + 60_000).toISOString(),
    }
    jest.mocked(workflowsCreateSyncOperation).mockResolvedValue(fresh)
    jest.mocked(workflowsGetSyncOperation).mockResolvedValue(fresh)
    await act(async () => {
      await result.current.pullWorkflows({ commit_sha: "abc", dry_run: true })
    })
    await waitFor(() =>
      expect(result.current.previewOperation?.id).toBe("fresh-operation")
    )
    expect(result.current.previewOperation?.status).toBe("ready")
  }
)

it.each([403, 404, 503, "network"] as const)(
  "blocks pull apply only after a permanent polling error (%s)",
  async (status) => {
    jest.useFakeTimers()
    const { result, unmount } = renderHook(
      () => useWorkflowSync("workspace-test"),
      { wrapper }
    )
    await act(async () => {
      await result.current.pullWorkflows({ commit_sha: "abc", dry_run: true })
    })
    await waitFor(() =>
      expect(result.current.previewOperation?.status).toBe("ready")
    )
    const callsBeforeError = jest.mocked(workflowsGetSyncOperation).mock.calls
      .length
    const error =
      status === "network"
        ? new TypeError("Network unavailable")
        : new ApiError(
            { method: "GET", url: "/sync" },
            {
              url: "/sync",
              ok: false,
              status,
              statusText: "Rejected",
              body: null,
            },
            "Rejected"
          )
    jest.mocked(workflowsGetSyncOperation).mockRejectedValue(error)
    await act(async () => {
      await jest.advanceTimersByTimeAsync(70_000)
    })
    expect(workflowsGetSyncOperation).toHaveBeenCalledTimes(
      callsBeforeError + (status === 403 || status === 404 ? 1 : 4)
    )
    if (status === 403 || status === 404) {
      expect(result.current.previewOperation).toBeUndefined()
      await act(async () => {
        await expect(
          result.current.pullWorkflows({ commit_sha: "abc" })
        ).rejects.toThrow("Preview expired or is no longer ready")
      })
      expect(workflowsApplySyncOperation).not.toHaveBeenCalled()
      const fresh = { ...operation, id: "fresh-operation" }
      jest.mocked(workflowsCreateSyncOperation).mockResolvedValue(fresh)
      jest.mocked(workflowsGetSyncOperation).mockResolvedValue(fresh)
      await act(async () => {
        await result.current.pullWorkflows({ commit_sha: "abc", dry_run: true })
      })
      await waitFor(() =>
        expect(result.current.previewOperation?.id).toBe("fresh-operation")
      )
    } else {
      expect(result.current.previewOperation?.status).toBe("ready")
      const completed: SyncOperationRead = {
        ...operation,
        status: "completed",
        stage: "finished",
        result: {
          success: true,
          commit_sha: "abc",
          workflows_found: 0,
          workflows_imported: 0,
          diagnostics: [],
          message: "Completed",
        },
      }
      jest.mocked(workflowsApplySyncOperation).mockResolvedValue(completed)
      jest.mocked(workflowsGetSyncOperation).mockResolvedValue(completed)
      await act(async () => {
        await result.current.pullWorkflows({ commit_sha: "abc" })
      })
      expect(workflowsApplySyncOperation).toHaveBeenCalledWith({
        workspaceId: "workspace-test",
        operationId: operation.id,
      })
    }
    unmount()
  }
)
