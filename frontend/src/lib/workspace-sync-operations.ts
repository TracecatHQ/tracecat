import {
  ApiError,
  type SyncOperationRead,
  workflowsGetSyncOperation,
} from "@/client"
import { type QueryClient, QueryObserver } from "@/lib/query"

/** Distinguish a temporary progress-read failure from a rejected operation. */
export function isTransientSyncError(error: unknown) {
  return (
    !(error instanceof ApiError) ||
    error.status === 408 ||
    error.status === 429 ||
    error.status >= 500
  )
}

/** Keep ready previews fresh until their server-provided expiration time. */
export function syncOperationPollInterval(
  operation?: SyncOperationRead,
  error?: unknown
): number | false {
  if (error && !isTransientSyncError(error)) return false
  if (
    !operation ||
    ["queued", "running", "applying"].includes(operation.status)
  ) {
    return 1500
  }
  if (
    operation.status === "ready" ||
    (operation.status === "failed" && operation.can_retry)
  ) {
    const remaining = Date.parse(operation.expires_at) - Date.now()
    return remaining > 0 ? Math.min(60_000, remaining) : false
  }
  return false
}

/** Never offer a cached preview or retry after its recorded deadline. */
export function expireSyncOperation(
  operation: SyncOperationRead
): SyncOperationRead {
  if (
    (operation.status === "ready" ||
      (operation.status === "failed" && operation.can_retry)) &&
    Date.parse(operation.expires_at) <= Date.now()
  ) {
    return { ...operation, status: "expired", can_retry: false }
  }
  return operation
}

/** Refresh resource caches once a pull's durable import receipt is visible. */
export function invalidateWorkspaceSyncResources(
  queryClient: QueryClient,
  workspaceId: string
) {
  const keys = [
    "workflows",
    "workflow-definitions",
    "workspace",
    "agent-presets",
    "agent-directory-items",
    "agent-tags",
    "agent-preset-workflow-references",
    "skills",
    "skill-library",
    "tables",
    "case-tag-catalog",
    "case-duration-definitions",
    "case-fields",
    "case-dropdown-definitions",
    "workspace-variables",
    "workspace-secrets",
  ]
  for (const key of keys) {
    void queryClient.invalidateQueries({ queryKey: [key, workspaceId] })
  }
}

type OperationPage = { items: SyncOperationRead[] }
type Observations = {
  stop: Map<string, () => void>
  applied: Set<string>
  historyInitialized: Set<string>
}
const observations = new WeakMap<QueryClient, Observations>()

function getObservations(client: QueryClient): Observations {
  const existing = observations.get(client)
  if (existing) return existing
  const state: Observations = {
    stop: new Map(),
    applied: new Set(),
    historyInitialized: new Set(),
  }
  observations.set(client, state)
  client.getQueryCache().subscribe((event) => {
    if (event.type !== "removed") return
    const [kind, workspaceId, operationId] = event.query.queryKey
    if (kind !== "sync-operation") return
    const key = JSON.stringify([workspaceId, operationId])
    state.stop.get(key)?.()
    // History can publish this completion again after its query is evicted.
    // Keep its receipt marker for this QueryClient's lifetime.
  })
  return state
}

function publishOperation(
  client: QueryClient,
  workspaceId: string,
  operation: SyncOperationRead,
  state: Observations
) {
  const key = JSON.stringify([workspaceId, operation.id])
  if (
    operation.direction === "pull" &&
    operation.data_applied &&
    !state.applied.has(key)
  ) {
    state.applied.add(key)
    invalidateWorkspaceSyncResources(client, workspaceId)
    // Composers observe this counter even when history is on another page.
    client.setQueryData<number>(
      ["workspace-sync-preview-revision", workspaceId],
      (revision = 0) => revision + 1
    )
  }
  if (
    operation.direction === "push" &&
    operation.status === "completed" &&
    !state.applied.has(key)
  ) {
    state.applied.add(key)
    // The branch head changed; other prepared pushes to it are now stale too.
    client.setQueryData<number>(
      ["workspace-sync-preview-revision", workspaceId],
      (revision = 0) => revision + 1
    )
    for (const query of ["workflow-sync-branches", "repository_commits"]) {
      void client.invalidateQueries({ queryKey: [query, workspaceId] })
    }
  }
  client.setQueriesData<OperationPage>(
    { queryKey: ["sync-operations", workspaceId] },
    (page) => {
      if (
        !page ||
        !page.items.some(
          (item) => item.id === operation.id && item !== operation
        )
      ) {
        return page
      }
      return {
        ...page,
        items: page.items.map((item) =>
          item.id === operation.id ? operation : item
        ),
      }
    }
  )
}

/** Seed historical receipts once, then publish new completions on latest-page refresh. */
export function observeSyncHistory(
  client: QueryClient,
  workspaceId: string,
  operations: readonly SyncOperationRead[],
  isLatestPage: boolean
) {
  const state = getObservations(client)
  const source =
    isLatestPage && state.historyInitialized.has(workspaceId)
      ? "live"
      : "history"
  // An empty first page is still a baseline. Keep it across panel remounts.
  if (isLatestPage) state.historyInitialized.add(workspaceId)
  for (const operation of operations) {
    observeSyncOperation(client, workspaceId, operation, source)
  }
}

/**
 * Observe only known, actor-authorized operations for this query client/workspace.
 * The subscription survives panel unmounts and pagination, ends at a terminal
 * status or permanent error, and is released when its cache entry is removed.
 */
export function observeSyncOperation(
  client: QueryClient,
  workspaceId: string,
  incoming: SyncOperationRead,
  source: "live" | "history" = "live"
) {
  const queryKey = ["sync-operation", workspaceId, incoming.id]
  const cached = client.getQueryData<SyncOperationRead>(queryKey)
  // SQL history can arrive after a newer authoritative workflow Query.
  const newest =
    cached && (cached.state_revision ?? 0) > (incoming.state_revision ?? 0)
      ? cached
      : incoming
  const operation = expireSyncOperation(newest)
  const state = getObservations(client)
  const key = JSON.stringify([workspaceId, operation.id])
  if (
    source === "history" &&
    !state.stop.has(key) &&
    ((operation.direction === "pull" && operation.data_applied) ||
      (operation.direction === "push" && operation.status === "completed"))
  ) {
    // An unseen old receipt is a baseline, not a new workspace change.
    // Known active observers and direct command responses still publish changes.
    state.applied.add(key)
  }
  if (client.getQueryData(queryKey) !== operation) {
    client.setQueryData(queryKey, operation)
  }
  publishOperation(client, workspaceId, operation, state)
  if (state.stop.has(key) || syncOperationPollInterval(operation) === false)
    return

  const observer = new QueryObserver(client, {
    queryKey,
    queryFn: async () =>
      expireSyncOperation(
        await workflowsGetSyncOperation({
          workspaceId,
          operationId: operation.id,
        })
      ),
    // A successful POST/list/GET already supplied the initial state.
    refetchOnMount: false,
    retry: (count, error) =>
      isTransientSyncError(error) &&
      count < 3 &&
      syncOperationPollInterval(client.getQueryData(queryKey), error) !== false,
    refetchInterval: (query) =>
      syncOperationPollInterval(query.state.data, query.state.error),
  })
  const stop = () => {
    observer.destroy()
    state.stop.delete(key)
    // End an in-flight retry batch as well as future polling intervals.
    void client.cancelQueries({ queryKey, exact: true }, { revert: false })
  }
  state.stop.set(key, stop)
  observer.subscribe((result) => {
    const current = result.data ? expireSyncOperation(result.data) : undefined
    if (current) {
      if (current !== result.data) client.setQueryData(queryKey, current)
      publishOperation(client, workspaceId, current, state)
    }
    if (syncOperationPollInterval(current, result.error) === false) stop()
  })
}
