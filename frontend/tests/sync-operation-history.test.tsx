import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"
import {
  type SyncOperationRead,
  workflowsApplySyncOperation,
  workflowsGetSyncDiff,
  workflowsGetSyncOperation,
  workflowsListSyncDiffs,
  workflowsListSyncOperations,
} from "@/client"
import { SyncOperationHistory } from "@/components/workspace-sync/sync-operation-history"
import { QueryClient, QueryClientProvider } from "@/lib/query"

jest.mock("@/client", () => ({
  ...jest.requireActual("@/client"),
  workflowsApplySyncOperation: jest.fn(),
  workflowsGetSyncDiff: jest.fn(),
  workflowsGetSyncOperation: jest.fn(),
  workflowsListSyncDiffs: jest.fn(),
  workflowsListSyncOperations: jest.fn(),
  workflowsRetrySyncOperation: jest.fn(),
}))
jest.mock("@/components/workspace-sync/unified-diff", () => ({
  UnifiedDiff: ({ diff }: { diff: string }) => <pre>{diff}</pre>,
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
  inputs: {
    id: "operation-test",
    direction: "push",
    push: { branch: "sync/test", message: "Sync test resources" },
  },
}
const firstDiff = {
  title: null,
  resource_type: "variable" as const,
  source_id: "example",
  source_path: "variables/example.yml",
  change_type: "modified" as const,
  diff: "",
}

function mountHistory() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  return render(
    <QueryClientProvider client={client}>
      <SyncOperationHistory workspaceId="workspace-test" />
    </QueryClientProvider>
  )
}

beforeEach(() => {
  jest.clearAllMocks()
  jest.mocked(workflowsGetSyncOperation).mockResolvedValue(operation)
  jest
    .mocked(workflowsListSyncOperations)
    .mockResolvedValue({ items: [operation] })
  jest.mocked(workflowsListSyncDiffs).mockResolvedValue({
    items: [firstDiff],
    next_cursor: "next-page",
  })
  jest.mocked(workflowsGetSyncDiff).mockResolvedValue({
    ...firstDiff,
    diff: "-before\n+after",
  })
  jest.mocked(workflowsApplySyncOperation).mockResolvedValue({
    ...operation,
    status: "applying",
    stage: "applying",
  })
})

it("recovers a ready preview after remount and confirms the persisted operation", async () => {
  const first = mountHistory()
  await screen.findByRole("button", { name: "Confirm push" })
  first.unmount()
  mountHistory()
  const confirm = await screen.findByRole("button", { name: "Confirm push" })
  expect(confirm).toBeDisabled()
  fireEvent.click(
    screen.getByRole("checkbox", { name: "Apply these prepared changes" })
  )
  fireEvent.click(confirm)
  await waitFor(() =>
    expect(workflowsApplySyncOperation).toHaveBeenCalledWith({
      workspaceId: "workspace-test",
      operationId: operation.id,
    })
  )
})

it("loads file contents only when selected and uses the next metadata page", async () => {
  mountHistory()
  const file = await screen.findByRole("button", {
    name: /variables\/example.yml/,
  })
  expect(workflowsGetSyncDiff).not.toHaveBeenCalled()
  expect(file).toHaveAttribute("aria-pressed", "false")
  fireEvent.click(file)
  expect(file).toHaveAttribute("aria-pressed", "true")
  await screen.findByText(/\+after/)
  expect(workflowsGetSyncDiff).toHaveBeenCalledWith({
    workspaceId: "workspace-test",
    operationId: operation.id,
    index: 0,
  })
  jest.mocked(workflowsListSyncDiffs).mockResolvedValueOnce({
    items: [{ ...firstDiff, source_path: "variables/last.yml" }],
  })
  fireEvent.click(screen.getByRole("button", { name: "Next files" }))
  fireEvent.click(
    await screen.findByRole("button", { name: /variables\/last.yml/ })
  )
  await waitFor(() =>
    expect(workflowsGetSyncDiff).toHaveBeenLastCalledWith({
      workspaceId: "workspace-test",
      operationId: operation.id,
      index: 50,
    })
  )
  expect(workflowsListSyncDiffs).toHaveBeenLastCalledWith({
    workspaceId: "workspace-test",
    operationId: operation.id,
    cursor: "next-page",
  })
})

it("does not offer confirmation or retry for an expired preview", async () => {
  jest.mocked(workflowsListSyncOperations).mockResolvedValue({
    items: [{ ...operation, status: "expired" }],
  })
  mountHistory()
  await screen.findByText("expired")
  expect(
    screen.queryByRole("button", { name: "Confirm push" })
  ).not.toBeInTheDocument()
  expect(
    screen.queryByRole("button", { name: "Retry failed stage" })
  ).not.toBeInTheDocument()
})

it("explains that a failed schedule sync has already imported the data", async () => {
  jest.mocked(workflowsListSyncOperations).mockResolvedValue({
    items: [
      {
        ...operation,
        direction: "pull",
        status: "failed",
        stage: "applying",
        data_applied: true,
        can_retry: true,
        diff_count: 0,
        inputs: {
          id: operation.id,
          direction: "pull",
          pull: { commit_sha: "a".repeat(40) },
        },
      },
    ],
  })
  mountHistory()
  expect(
    await screen.findByText(/Workspace changes were imported/)
  ).toBeInTheDocument()
  expect(
    screen.getByRole("button", { name: "Retry failed stage" })
  ).toBeEnabled()
})

it.each([
  ["push", "new-commit", "new-commit"],
  ["push", null, "source-commit"],
  ["pull", null, "source-commit"],
] as const)(
  "shows the correct commit for %s with result SHA %s",
  async (direction, sha, expected) => {
    jest.mocked(workflowsListSyncOperations).mockResolvedValue({
      items: [
        {
          ...operation,
          direction,
          status: "completed",
          stage: "finished",
          diff_count: 0,
          commit_sha: "source-commit",
          result:
            direction === "push"
              ? {
                  commit: {
                    status: sha ? "committed" : "no_op",
                    sha,
                    ref: "sync/test",
                    base_ref: "main",
                  },
                  files: [],
                }
              : null,
        },
      ],
    })
    mountHistory()
    expect(await screen.findByText(`Commit ${expected}`)).toBeInTheDocument()
    if (sha)
      expect(screen.queryByText("Commit source-commit")).not.toBeInTheDocument()
  }
)

it("stops polling history when every operation is terminal", async () => {
  jest.useFakeTimers()
  try {
    jest.mocked(workflowsListSyncOperations).mockResolvedValue({
      items: [
        { ...operation, status: "completed", stage: "finished", diff_count: 0 },
      ],
    })
    const view = mountHistory()
    await screen.findByRole("heading", { name: "Sync operations" })
    const calls = jest.mocked(workflowsListSyncOperations).mock.calls.length
    await act(async () => {
      jest.advanceTimersByTime(6000)
    })
    expect(workflowsListSyncOperations).toHaveBeenCalledTimes(calls)
    view.unmount()
  } finally {
    jest.useRealTimers()
  }
})

it("resets the visible selection and review confirmation across history pages", async () => {
  const first = {
    ...operation,
    id: "first",
    diff_count: 0,
    created_at: "2026-01-03T00:00:00Z",
  }
  const second = {
    ...operation,
    id: "second",
    diff_count: 0,
    created_at: "2026-01-02T00:00:00Z",
  }
  const older = {
    ...operation,
    id: "older",
    diff_count: 0,
    created_at: "2026-01-01T00:00:00Z",
  }
  jest
    .mocked(workflowsListSyncOperations)
    .mockResolvedValueOnce({ items: [first, second], next_cursor: "older" })
    .mockResolvedValueOnce({ items: [older], prev_cursor: "newer" })
    .mockResolvedValue({ items: [first, second], next_cursor: "older" })
  mountHistory()
  let rows = await screen.findAllByRole("button", { name: /^Push ·/ })
  fireEvent.click(rows[1])
  expect(rows[1]).toHaveAttribute("aria-pressed", "true")
  fireEvent.click(
    screen.getByRole("checkbox", { name: "Apply these prepared changes" })
  )
  expect(screen.getByRole("button", { name: "Confirm push" })).toBeEnabled()
  fireEvent.click(screen.getByRole("button", { name: "Older" }))
  await screen.findByRole("button", { name: "Newer" })
  expect(screen.getByRole("button", { name: /^Push ·/ })).toHaveAttribute(
    "aria-pressed",
    "true"
  )
  expect(screen.getByRole("button", { name: "Confirm push" })).toBeDisabled()
  fireEvent.click(screen.getByRole("button", { name: "Newer" }))
  rows = await screen.findAllByRole("button", { name: /^Push ·/ })
  expect(rows[0]).toHaveAttribute("aria-pressed", "true")
  expect(rows[1]).toHaveAttribute("aria-pressed", "false")
})

it("keeps observing an active operation while viewing an older terminal page", async () => {
  jest.useFakeTimers()
  try {
    const active = {
      ...operation,
      diff_count: 0,
      status: "running" as const,
      stage: "preparing" as const,
    }
    const completed = {
      ...operation,
      id: "older",
      diff_count: 0,
      status: "completed" as const,
      stage: "finished" as const,
    }
    jest
      .mocked(workflowsListSyncOperations)
      .mockResolvedValueOnce({ items: [active], next_cursor: "older" })
      .mockResolvedValue({ items: [completed], prev_cursor: "newer" })
    jest.mocked(workflowsGetSyncOperation).mockResolvedValue({
      ...active,
      status: "ready",
      stage: "awaiting_confirmation",
    })
    const view = mountHistory()
    fireEvent.click(await screen.findByRole("button", { name: "Older" }))
    await screen.findByRole("button", { name: "Newer" })
    await act(async () => {
      await jest.advanceTimersByTimeAsync(1500)
    })
    expect(workflowsGetSyncOperation).toHaveBeenCalledWith({
      workspaceId: "workspace-test",
      operationId: operation.id,
    })
    view.unmount()
  } finally {
    jest.useRealTimers()
  }
})

it("removes confirmation when a mounted ready preview expires", async () => {
  jest.useFakeTimers()
  try {
    const ready = {
      ...operation,
      diff_count: 0,
      expires_at: new Date(Date.now() + 2000).toISOString(),
    }
    jest
      .mocked(workflowsListSyncOperations)
      .mockResolvedValue({ items: [ready] })
    jest
      .mocked(workflowsGetSyncOperation)
      .mockResolvedValue({ ...ready, status: "expired" })
    const view = mountHistory()
    await screen.findByRole("button", { name: "Confirm push" })
    fireEvent.click(
      screen.getByRole("checkbox", { name: "Apply these prepared changes" })
    )
    await act(async () => {
      await jest.advanceTimersByTimeAsync(2000)
    })
    expect(
      screen.queryByRole("button", { name: "Confirm push" })
    ).not.toBeInTheDocument()
    expect(
      screen.getByText("Preview expired. Start a fresh preview.")
    ).toBeInTheDocument()
    view.unmount()
  } finally {
    jest.useRealTimers()
  }
})
