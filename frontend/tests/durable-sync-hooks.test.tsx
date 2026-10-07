import { act, renderHook, waitFor } from "@testing-library/react"
import type { ReactNode } from "react"
import {
  ApiError,
  type SyncOperationRead,
  workflowsCreateSyncOperation,
  workflowsGetSyncOperation,
} from "@/client"
import {
  SyncOperationPollingError,
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
}))

const operation: SyncOperationRead = {
  id: "operation-test",
  direction: "pull",
  status: "ready",
  stage: "awaiting_confirmation",
  created_at: "2026-01-01T00:00:00Z",
  expires_at: "2026-01-02T00:00:00Z",
  diff_count: 0,
  can_retry: false,
  inputs: {
    id: "operation-test",
    direction: "pull",
    pull: { commit_sha: "abc", dry_run: true },
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
  jest
    .mocked(workflowsCreateSyncOperation)
    .mockResolvedValue({ ...operation, status: "queued" })
  jest
    .mocked(workflowsGetSyncOperation)
    .mockRejectedValueOnce(new TypeError("Response lost"))
  const { result } = renderHook(
    () =>
      useWorkspaceSyncExportPreview("workspace-test", {
        push: { branch: "sync/test", message: "Sync" },
      }),
    { wrapper }
  )
  act(() => result.current.refetchPreview())
  await waitFor(() => expect(result.current.previewError).toBeTruthy())
  expect(result.current.previewIsLoading).toBe(false)
  act(() => result.current.refetchPreview())
  await waitFor(() =>
    expect(result.current.previewOperationId).toBe(operation.id)
  )
  expect(workflowsCreateSyncOperation).toHaveBeenCalledTimes(1)
})
