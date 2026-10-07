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
  if (operation.status === "ready") {
    return Math.max(
      1,
      Math.min(60_000, Date.parse(operation.expires_at) - Date.now())
    )
  }
  return false
}

/** Never offer a cached preview after its confirmation window has elapsed. */
export function expireSyncOperation(
  operation: SyncOperationRead
): SyncOperationRead {
  if (
    operation.status === "ready" &&
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
}
const observations = new WeakMap<QueryClient, Observations>()

function getObservations(client: QueryClient): Observations {
  const existing = observations.get(client)
  if (existing) return existing
  const state: Observations = { stop: new Map(), applied: new Set() }
  observations.set(client, state)
  client.getQueryCache().subscribe((event) => {
    if (event.type !== "removed") return
    const [kind, workspaceId, operationId] = event.query.queryKey
    if (kind !== "sync-operation") return
    const key = JSON.stringify([workspaceId, operationId])
    state.stop.get(key)?.()
    state.applied.delete(key)
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

/**
 * Observe only known, actor-authorized operations for this query client/workspace.
 * The subscription survives panel unmounts and pagination, ends at a terminal
 * status or permanent error, and is released when its cache entry is removed.
 */
export function observeSyncOperation(
  client: QueryClient,
  workspaceId: string,
  incoming: SyncOperationRead
) {
  const operation = expireSyncOperation(incoming)
  const state = getObservations(client)
  const key = JSON.stringify([workspaceId, operation.id])
  const queryKey = ["sync-operation", workspaceId, operation.id]
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
    retry: (count, error) => isTransientSyncError(error) && count < 3,
    refetchInterval: (query) =>
      syncOperationPollInterval(query.state.data, query.state.error),
  })
  const stop = () => {
    observer.destroy()
    state.stop.delete(key)
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
