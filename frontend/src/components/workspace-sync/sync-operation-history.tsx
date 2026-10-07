"use client"

import { useEffect, useState } from "react"
import {
  type SyncOperationRead,
  workflowsApplySyncOperation,
  workflowsListSyncOperations,
  workflowsRetrySyncOperation,
} from "@/client"
import { Button } from "@/components/ui/button"
import { OperationDiffs } from "@/components/workspace-sync/sync-operation-diffs"
import { useMutation, useQuery, useQueryClient } from "@/lib/query"
import { cn } from "@/lib/utils"
import {
  expireSyncOperation,
  observeSyncOperation,
} from "@/lib/workspace-sync-operations"

const STAGES = {
  fetching: "Fetching files",
  preparing: "Preparing changes",
  awaiting_confirmation: "Ready to review",
  applying: "Applying changes",
  finished: "Completed",
} as const

/** Persistent, paginated operation history with resumable confirmation and lazy diffs. */
export function SyncOperationHistory({ workspaceId }: { workspaceId: string }) {
  const client = useQueryClient()
  const [cursor, setCursor] = useState<string>()
  const [selected, setSelected] = useState<string>()
  const { data, error } = useQuery({
    queryKey: ["sync-operations", workspaceId, cursor],
    queryFn: () =>
      workflowsListSyncOperations({ workspaceId, cursor, limit: 10 }),
  })
  useEffect(() => {
    for (const operation of data?.items ?? []) {
      observeSyncOperation(client, workspaceId, operation)
    }
  }, [client, workspaceId, data])
  function changePage(nextCursor?: string | null) {
    setSelected(undefined)
    setCursor(nextCursor ?? undefined)
  }

  if (error)
    return (
      <p className="text-sm text-destructive">
        Unable to load sync operations. Reload to reconnect.
      </p>
    )
  if (!data?.items.length) return null
  const operation = expireSyncOperation(
    data.items.find((item) => item.id === selected) ?? data.items[0]
  )
  return (
    <section className="space-y-3 border-t pt-5">
      <h3 className="text-sm font-medium">Sync operations</h3>
      <p className="text-xs text-muted-foreground">
        Sync continues if you leave this page. Unconfirmed previews expire after
        24 hours.
      </p>
      <div className="divide-y rounded-md border">
        {data.items.map((item) => (
          <button
            key={item.id}
            type="button"
            onClick={() => setSelected(item.id)}
            aria-pressed={operation.id === item.id}
            className={cn(
              "flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-xs hover:bg-muted",
              operation.id === item.id && "bg-muted"
            )}
          >
            <span>
              {item.direction === "push" ? "Push" : "Pull"} ·{" "}
              {new Date(item.created_at).toLocaleString()}
            </span>
            <span>
              {["failed", "expired"].includes(item.status)
                ? item.status
                : STAGES[item.stage]}
            </span>
          </button>
        ))}
      </div>
      <div className="flex gap-2">
        {data.prev_cursor && (
          <Button
            size="sm"
            variant="outline"
            onClick={() => changePage(data.prev_cursor)}
          >
            Newer
          </Button>
        )}
        {data.next_cursor && (
          <Button
            size="sm"
            variant="outline"
            onClick={() => changePage(data.next_cursor)}
          >
            Older
          </Button>
        )}
      </div>
      <OperationDetails
        key={operation.id}
        workspaceId={workspaceId}
        operation={operation}
      />
    </section>
  )
}

function OperationDetails({
  workspaceId,
  operation,
}: {
  workspaceId: string
  operation: SyncOperationRead
}) {
  const client = useQueryClient()
  const [reviewed, setReviewed] = useState(false)
  const action = useMutation({
    mutationFn: (kind: "apply" | "retry") =>
      kind === "apply"
        ? workflowsApplySyncOperation({
            workspaceId,
            operationId: operation.id,
          })
        : workflowsRetrySyncOperation({
            workspaceId,
            operationId: operation.id,
          }),
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
  const push = operation.inputs.push
  const preview = operation.preview
  const result = operation.result
  const prUrl = result && "commit" in result ? result.commit.pr_url : null
  let commitSha = operation.commit_sha
  if (
    operation.direction === "push" &&
    operation.status === "completed" &&
    result &&
    "commit" in result &&
    result.commit.sha
  ) {
    commitSha = result.commit.sha
  }
  return (
    <div className="space-y-3 text-sm">
      <p>
        {operation.status === "failed"
          ? `Failed: ${STAGES[operation.stage]}`
          : operation.status === "expired"
            ? "Preview expired. Start a fresh preview."
            : STAGES[operation.stage]}
      </p>
      {push && (
        <div className="space-y-1 text-xs">
          <p>Target branch: {push.branch}</p>
          <p>{push.message}</p>
          <p>
            {push.create_pr
              ? `Open a review request against ${push.pr_base_branch ?? "the repository default branch"}`
              : "Commit directly to the target branch"}
          </p>
        </div>
      )}
      {commitSha && <p className="font-mono text-xs">Commit {commitSha}</p>}
      {operation.error && <p className="text-destructive">{operation.error}</p>}
      {operation.data_applied && operation.status !== "completed" && (
        <p>
          Workspace changes were imported. Schedule synchronization is still
          pending. Retrying resumes schedule synchronization without importing
          again.
        </p>
      )}
      {preview &&
        "diagnostics" in preview &&
        preview.diagnostics.length > 0 && (
          <p className="text-destructive">
            {preview.message} Review the mapping choices in Pull and start a new
            preview.
          </p>
        )}
      {prUrl && (
        <a className="underline" href={prUrl} target="_blank" rel="noreferrer">
          View review request
        </a>
      )}
      {result && "message" in result && <p>{result.message}</p>}
      {(operation.diff_count ?? 0) > 0 && (
        <OperationDiffs
          workspaceId={workspaceId}
          operationId={operation.id}
          count={operation.diff_count ?? 0}
        />
      )}
      {operation.status === "ready" && (
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 text-xs">
            <input
              type="checkbox"
              checked={reviewed}
              onChange={(event) => setReviewed(event.target.checked)}
            />
            Apply these prepared changes
          </label>
          <Button
            size="sm"
            disabled={!reviewed || action.isPending}
            onClick={() => action.mutate("apply")}
          >
            {operation.direction === "push" ? "Confirm push" : "Apply pull"}
          </Button>
        </div>
      )}
      {operation.can_retry && (
        <Button
          size="sm"
          variant="outline"
          disabled={action.isPending}
          onClick={() => action.mutate("retry")}
        >
          Retry failed stage
        </Button>
      )}
      {action.error && (
        <p className="text-destructive">
          Unable to start. Refresh the operation and try again.
        </p>
      )}
    </div>
  )
}
