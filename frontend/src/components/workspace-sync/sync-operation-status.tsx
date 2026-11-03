"use client"

import { Loader2Icon } from "lucide-react"
import { useEffect } from "react"
import { type SyncOperationRead, workflowsListSyncOperations } from "@/client"
import { Button } from "@/components/ui/button"
import { GitSyncNotice } from "@/components/workspace-sync/git-sync-change-list"
import { useMutation, useQuery, useQueryClient } from "@/lib/query"
import { sendSyncOperationCommand } from "@/lib/workspace-sync-command"
import {
  expireSyncOperation,
  observeSyncHistory,
  observeSyncOperation,
} from "@/lib/workspace-sync-operations"

/**
 * One line about the latest push or pull when it still needs attention: it is
 * applying in the background, or it failed and can resume. Recent operations
 * are observed so background completions refresh the page.
 */
export function SyncOperationStatus({ workspaceId }: { workspaceId: string }) {
  const client = useQueryClient()
  const { data } = useQuery({
    queryKey: ["sync-operations", workspaceId, undefined],
    queryFn: () => workflowsListSyncOperations({ workspaceId, limit: 10 }),
  })
  useEffect(() => {
    if (!data) return
    observeSyncHistory(client, workspaceId, data.items, true)
  }, [client, workspaceId, data])

  const latest = data?.items[0]
  if (!latest) return null
  const operation = expireSyncOperation(latest)
  if (operation.status === "applying") {
    return (
      <GitSyncNotice tone="info">
        <span className="flex items-center gap-2">
          <Loader2Icon className="size-3.5 shrink-0 animate-spin" />
          {getApplyingMessage(operation)}
        </span>
      </GitSyncNotice>
    )
  }
  if (operation.status === "failed" && operation.can_retry) {
    return (
      <RetryNotice
        key={operation.id}
        workspaceId={workspaceId}
        operation={operation}
      />
    )
  }
  return null
}

function RetryNotice({
  workspaceId,
  operation,
}: {
  workspaceId: string
  operation: SyncOperationRead
}) {
  const client = useQueryClient()
  const retry = useMutation({
    mutationFn: () => sendSyncOperationCommand(workspaceId, operation, "retry"),
    onSuccess: (updated) => {
      observeSyncOperation(client, workspaceId, updated)
      void client.invalidateQueries({
        queryKey: ["sync-operations", workspaceId],
      })
    },
    onError: () => {
      void client.invalidateQueries({
        queryKey: ["sync-operations", workspaceId],
      })
    },
  })
  const direction = operation.direction === "push" ? "push" : "pull"
  return (
    <GitSyncNotice tone="error">
      <span className="flex items-center gap-3">
        <span className="min-w-0 flex-1">
          Last {direction} did not finish.{" "}
          {operation.error ?? "Retry to resume it."}
          {operation.data_applied &&
            " Workspace changes were already imported and will not be imported again."}
          {retry.error && " Could not retry. Refresh and try again."}
        </span>
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="h-7 shrink-0"
          disabled={retry.isPending}
          onClick={() => retry.mutate()}
        >
          Retry
        </Button>
      </span>
    </GitSyncNotice>
  )
}

function getApplyingMessage(operation: SyncOperationRead): string {
  if (operation.direction === "push") {
    const branch = operation.inputs.push?.branch
    return `Pushing${branch ? ` to ${branch}` : ""} in the background. You can leave this page.`
  }
  if (operation.data_applied) {
    return "Pull imported. Finishing schedules and background work."
  }
  return "Pulling into the workspace in the background. You can leave this page."
}
