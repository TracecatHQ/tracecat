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
import { invalidateWorkspaceSyncResources } from "@/hooks/use-workspace-sync"
import { useMutation, useQuery, useQueryClient } from "@/lib/query"

const STAGES = {
  fetching: "Fetching files",
  preparing: "Preparing changes",
  awaiting_confirmation: "Ready to review",
  applying: "Applying changes",
  finished: "Completed",
} as const

/** Persistent, paginated operation history with resumable confirmation and lazy diffs. */
export function SyncOperationHistory({ workspaceId }: { workspaceId: string }) {
  const [cursor, setCursor] = useState<string>()
  const [selected, setSelected] = useState<string>()
  const { data, error } = useQuery({
    queryKey: ["sync-operations", workspaceId, cursor],
    queryFn: () =>
      workflowsListSyncOperations({ workspaceId, cursor, limit: 10 }),
    refetchInterval: (query) =>
      query.state.data?.items.some((item) =>
        ["queued", "running", "applying"].includes(item.status)
      )
        ? 2000
        : false,
  })
  if (error)
    return (
      <p className="text-sm text-destructive">
        Unable to load sync operations. Reload to reconnect.
      </p>
    )
  if (!data?.items.length) return null
  const operation =
    data.items.find((item) => item.id === selected) ?? data.items[0]
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
            className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-xs hover:bg-muted"
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
            onClick={() => setCursor(data.prev_cursor ?? undefined)}
          >
            Newer
          </Button>
        )}
        {data.next_cursor && (
          <Button
            size="sm"
            variant="outline"
            onClick={() => setCursor(data.next_cursor ?? undefined)}
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
    onSuccess: () =>
      client.invalidateQueries({ queryKey: ["sync-operations", workspaceId] }),
  })
  useEffect(() => {
    if (operation.direction === "pull" && operation.data_applied) {
      invalidateWorkspaceSyncResources(client, workspaceId)
    }
  }, [
    client,
    workspaceId,
    operation.id,
    operation.direction,
    operation.data_applied,
  ])
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
