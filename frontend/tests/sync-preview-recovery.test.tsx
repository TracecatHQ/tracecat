import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import {
  type SyncOperationRead,
  workflowsCreateSyncOperation,
  workflowsGetSyncDiff,
  workflowsGetSyncOperation,
  workflowsListSyncDiffs,
} from "@/client"
import { OperationDiffs } from "@/components/workspace-sync/sync-operation-diffs"
import { useWorkspaceSyncExportPreview } from "@/hooks/use-workspace-sync"
import { QueryClient, QueryClientProvider } from "@/lib/query"

jest.mock("@/client", () => ({
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
  expires_at: "2026-01-02T00:00:00Z",
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
      enabled: false,
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
