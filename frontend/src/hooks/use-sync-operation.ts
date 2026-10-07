import { useEffect, useRef } from "react"
import { type SyncOperationRead, workflowsGetSyncOperation } from "@/client"
import { useQuery } from "@/lib/query"
import {
  expireSyncOperation,
  isTransientSyncError,
  syncOperationPollInterval,
} from "@/lib/workspace-sync-operations"

/** An accepted job is still durable even when its status cannot be read. */
export class SyncOperationPollingError extends Error {
  constructor() {
    super(
      "Unable to read sync progress. The accepted operation may still be running. Reopen Git sync to check its status."
    )
    this.name = "SyncOperationPollingError"
  }
}

/** Poll one durable phase while this view is mounted; leaving never cancels the job. */
export function useSyncOperationWaiter(workspaceId: string) {
  const mounted = useRef(true)
  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])
  return async function waitForOperation(
    id: string
  ): Promise<SyncOperationRead> {
    let failures = 0
    while (mounted.current) {
      let operation: SyncOperationRead
      try {
        operation = await workflowsGetSyncOperation({
          workspaceId,
          operationId: id,
        })
        failures = 0
      } catch (error) {
        if (!isTransientSyncError(error)) throw error
        if (++failures > 3) throw new SyncOperationPollingError()
        await new Promise((resolve) =>
          setTimeout(resolve, 1000 * 2 ** (failures - 1))
        )
        continue
      }
      if (!["queued", "running", "applying"].includes(operation.status))
        return operation
      await new Promise((resolve) => setTimeout(resolve, 1500))
    }
    throw new Error(
      "Sync continues in the background. Reopen Git sync to see the result."
    )
  }
}

/** Observe an accepted job; retries only read the same operation. */
export function useSyncOperation(workspaceId: string, operationId?: string) {
  return useQuery({
    queryKey: ["sync-operation", workspaceId, operationId],
    queryFn: async () =>
      expireSyncOperation(
        await workflowsGetSyncOperation({
          workspaceId,
          operationId: operationId!,
        })
      ),
    enabled: Boolean(operationId),
    retry: (count, error) => isTransientSyncError(error) && count < 3,
    refetchInterval: (query) =>
      syncOperationPollInterval(query.state.data, query.state.error),
  })
}
