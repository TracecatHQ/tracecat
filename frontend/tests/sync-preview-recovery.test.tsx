import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { useState } from "react"
import {
  type SyncOperationRead,
  workflowsCreateSyncOperation,
  workflowsGetSyncDiff,
  workflowsGetSyncOperation,
  workflowsListSyncDiffs,
} from "@/client"
import { PushResourcePreview } from "@/components/workspace-sync/resource-diff-review"
import { OperationDiffs } from "@/components/workspace-sync/sync-operation-diffs"
import { useWorkspaceSyncExportPreview } from "@/hooks/use-workspace-sync"
import { QueryClient, QueryClientProvider } from "@/lib/query"

jest.mock("@/client", () => ({
  ...jest.requireActual("@/client"),
  workflowsCreateSyncOperation: jest.fn(),
  workflowsGetSyncOperation: jest.fn(),
  workflowsListSyncDiffs: jest.fn(),
  workflowsGetSyncDiff: jest.fn(),
}))
jest.mock("@/components/workspace-sync/unified-diff", () => ({
  UnifiedDiff: ({ diff }: { diff: string }) => <pre>{diff}</pre>,
}))

const operation: SyncOperationRead = {
  id: "operation-example",
  direction: "push",
  status: "queued",
  stage: "fetching",
  created_at: "2026-01-01T00:00:00Z",
  expires_at: "2099-01-02T00:00:00Z",
  diff_count: 0,
  can_retry: false,
  inputs: {
    id: "operation-example",
    direction: "push",
    push: { branch: "main", message: "Example" },
  },
}
function Preview() {
  const { refetchPreview, previewOperationId, previewError } =
    useWorkspaceSyncExportPreview("workspace-example", {
      push: { branch: "main", message: "Example" },
    })
  return (
    <>
      <button type="button" onClick={refetchPreview}>
        Preview
      </button>
      <p>{previewOperationId}</p>
      <p>{previewError?.message}</p>
    </>
  )
}
function client() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: 2, retryDelay: 0, gcTime: 0 },
      mutations: { retry: false },
    },
  })
}
beforeEach(() => {
  jest.resetAllMocks()
  Object.defineProperty(crypto, "randomUUID", {
    configurable: true,
    value: jest.fn(() => "00000000-0000-4000-8000-000000000001"),
  })
  jest.mocked(workflowsCreateSyncOperation).mockResolvedValue(operation)
})
it("retries GET polling without creating another preview", async () => {
  jest
    .mocked(workflowsGetSyncOperation)
    .mockRejectedValueOnce(new Error("Network error"))
    .mockResolvedValue({ ...operation, status: "ready" })
  render(
    <QueryClientProvider client={client()}>
      <Preview />
    </QueryClientProvider>
  )
  fireEvent.click(screen.getByText("Preview"))
  await screen.findByText(operation.id)
  expect(workflowsCreateSyncOperation).toHaveBeenCalledTimes(1)
  expect(workflowsGetSyncOperation).toHaveBeenCalledTimes(2)
  expect(workflowsGetSyncOperation).toHaveBeenLastCalledWith({
    workspaceId: "workspace-example",
    operationId: operation.id,
  })
})
it("renders a terminal failure without retrying creation", async () => {
  jest.mocked(workflowsGetSyncOperation).mockResolvedValue({
    ...operation,
    status: "failed",
    error: "Validation failed",
  })
  render(
    <QueryClientProvider client={client()}>
      <Preview />
    </QueryClientProvider>
  )
  fireEvent.click(screen.getByText("Preview"))
  await screen.findByText("Validation failed")
  expect(workflowsCreateSyncOperation).toHaveBeenCalledTimes(1)
  expect(workflowsGetSyncOperation).toHaveBeenCalledTimes(1)
})
it("reuses the creation ID after a lost POST response", async () => {
  jest
    .mocked(workflowsCreateSyncOperation)
    .mockRejectedValueOnce(new Error("Lost response"))
  jest
    .mocked(workflowsGetSyncOperation)
    .mockResolvedValue({ ...operation, status: "ready" })
  render(
    <QueryClientProvider client={client()}>
      <Preview />
    </QueryClientProvider>
  )
  fireEvent.click(screen.getByText("Preview"))
  await screen.findByText("Lost response")
  fireEvent.click(screen.getByText("Preview"))
  await screen.findByText(operation.id)
  const calls = jest.mocked(workflowsCreateSyncOperation).mock.calls
  expect(calls[0][0].requestBody.id).toBe(calls[1][0].requestBody.id)
})
it("resets the page and selection when a refreshed preview replaces the operation", async () => {
  const diff = {
    source_path: "variables/example.yml",
    resource_type: "variable" as const,
    source_id: "example",
    title: null,
    change_type: "modified" as const,
    diff: "",
  }
  jest
    .mocked(workflowsListSyncDiffs)
    .mockResolvedValue({ items: [diff], next_cursor: "next-page" })
  jest
    .mocked(workflowsGetSyncDiff)
    .mockResolvedValue({ ...diff, diff: "old selected contents" })
  const queryClient = client()
  const { rerender } = render(
    <QueryClientProvider client={queryClient}>
      <OperationDiffs
        workspaceId="workspace-example"
        operationId="old"
        count={101}
      />
    </QueryClientProvider>
  )
  fireEvent.click(await screen.findByText("Next files"))
  await waitFor(() =>
    expect(workflowsListSyncDiffs).toHaveBeenCalledWith({
      workspaceId: "workspace-example",
      operationId: "old",
      cursor: "next-page",
    })
  )
  fireEvent.click(
    await screen.findByRole("button", { name: /variables\/example.yml/ })
  )
  await screen.findByText("old selected contents")
  jest.mocked(workflowsListSyncDiffs).mockResolvedValue({ items: [diff] })
  rerender(
    <QueryClientProvider client={queryClient}>
      <OperationDiffs
        workspaceId="workspace-example"
        operationId="new"
        count={1}
      />
    </QueryClientProvider>
  )
  await waitFor(() =>
    expect(workflowsListSyncDiffs).toHaveBeenLastCalledWith({
      workspaceId: "workspace-example",
      operationId: "new",
      cursor: undefined,
    })
  )
  expect(screen.queryByText("Previous files")).not.toBeInTheDocument()
  expect(screen.queryByText("old selected contents")).not.toBeInTheDocument()
  expect(workflowsGetSyncDiff).toHaveBeenCalledTimes(1)
})

function PushPreview() {
  const [requested, setRequested] = useState(false)
  const preview = useWorkspaceSyncExportPreview("workspace-example", {
    push: { branch: "main", message: "Example" },
  })
  return (
    <>
      <PushResourcePreview
        preview={preview.preview}
        isLoading={preview.previewIsLoading}
        compareRef="main"
        hasRequestedPreview={requested}
        errorMessage={preview.previewError?.message}
        onRequestPreview={() => {
          setRequested(true)
          preview.refetchPreview()
        }}
      />
      <button type="button" disabled={!preview.previewOperationId}>
        Push
      </button>
    </>
  )
}

it.each([false, true])(
  "keeps expired push previews refreshable (read failure: %s)",
  async (readFailure) => {
    jest.useFakeTimers()
    const queryClient = client()
    try {
      const ready: SyncOperationRead = {
        ...operation,
        status: "ready",
        expires_at: new Date(Date.now() + 2000).toISOString(),
        preview: {
          files: [],
          resources: [],
          resource_counts: {},
          resource_diffs: [],
        },
      }
      jest.mocked(workflowsCreateSyncOperation).mockResolvedValue(ready)
      jest.mocked(workflowsGetSyncOperation).mockResolvedValue(ready)
      const view = render(
        <QueryClientProvider client={queryClient}>
          <PushPreview />
        </QueryClientProvider>
      )
      fireEvent.click(screen.getByRole("button", { name: "Preview changes" }))
      await waitFor(() =>
        expect(screen.getByRole("button", { name: "Push" })).toBeEnabled()
      )
      if (readFailure)
        jest
          .mocked(workflowsGetSyncOperation)
          .mockRejectedValue(new Error("Offline"))
      await act(async () => {
        await jest.advanceTimersByTimeAsync(2100)
      })
      expect(screen.getByRole("button", { name: "Push" })).toBeDisabled()
      expect(
        screen.getByText(/Preview expired. Refresh the preview before pushing./)
      ).toBeVisible()
      const refresh = screen.getByRole("button", { name: "Refresh preview" })
      expect(refresh).toBeEnabled()
      const fresh = {
        ...ready,
        id: "fresh-operation",
        expires_at: new Date(Date.now() + 60_000).toISOString(),
      }
      jest
        .mocked(crypto.randomUUID)
        .mockReturnValue("00000000-0000-4000-8000-000000000002")
      jest.mocked(workflowsCreateSyncOperation).mockResolvedValue(fresh)
      jest.mocked(workflowsGetSyncOperation).mockResolvedValue(fresh)
      fireEvent.click(refresh)
      await waitFor(() =>
        expect(workflowsCreateSyncOperation).toHaveBeenCalledTimes(2)
      )
      expect(
        jest.mocked(workflowsCreateSyncOperation).mock.calls[1][0].requestBody
          .id
      ).toBe("00000000-0000-4000-8000-000000000002")
      await waitFor(() =>
        expect(screen.getByRole("button", { name: "Push" })).toBeEnabled()
      )
      view.unmount()
    } finally {
      queryClient.clear()
      jest.useRealTimers()
    }
  }
)
