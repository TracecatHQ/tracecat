import { useEffect, useRef } from "react"
import {
  ApiError,
  type SyncOperationRead,
  workflowsGetSyncOperation,
} from "@/client"
import { useQuery } from "@/lib/query"

/** An accepted job is still durable even when its status cannot be read. */
export class SyncOperationPollingError extends Error {
  constructor() {
    super(
      "Unable to read sync progress. The accepted operation may still be running. Reopen Git sync to check its status."
    )
    this.name = "SyncOperationPollingError"
  }
}

function isTransientPollingError(error: unknown) {
  return (
    !(error instanceof ApiError) ||
    error.status === 408 ||
    error.status === 429 ||
    error.status >= 500
  )
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
        if (!isTransientPollingError(error)) throw error
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
    queryFn: () =>
      workflowsGetSyncOperation({
        workspaceId,
        operationId: operationId!,
      }),
    enabled: Boolean(operationId),
    refetchInterval: (query) =>
      !query.state.data ||
      ["queued", "running", "applying"].includes(query.state.data.status)
        ? 1500
        : false,
  })
}
