"use client"

import { useState } from "react"
import { workflowsGetSyncDiff, workflowsListSyncDiffs } from "@/client"
import { Button } from "@/components/ui/button"
import { UnifiedDiff } from "@/components/workspace-sync/unified-diff"
import { useQuery } from "@/lib/query"
import { cn } from "@/lib/utils"

/** Load bounded diff pages and fetch selected file contents on demand. */
type OperationDiffsProps = {
  workspaceId: string
  operationId: string
  count: number
}

export function OperationDiffs(props: OperationDiffsProps) {
  return (
    <OperationDiffPages
      key={`${props.workspaceId}/${props.operationId}`}
      {...props}
    />
  )
}

function OperationDiffPages({
  workspaceId,
  operationId,
  count,
}: OperationDiffsProps) {
  const [pages, setPages] = useState<Array<string | undefined>>([undefined])
  const [selected, setSelected] = useState<number>()
  const pageIndex = pages.length - 1
  const { data, isLoading, error } = useQuery({
    queryKey: [
      "sync-operation-diffs",
      workspaceId,
      operationId,
      pages[pageIndex],
    ],
    queryFn: () =>
      workflowsListSyncDiffs({
        workspaceId,
        operationId,
        cursor: pages[pageIndex],
      }),
  })
  const {
    data: diff,
    isLoading: diffIsLoading,
    error: diffError,
  } = useQuery({
    queryKey: ["sync-operation-diff", workspaceId, operationId, selected],
    queryFn: () =>
      workflowsGetSyncDiff({ workspaceId, operationId, index: selected ?? 0 }),
    enabled: selected !== undefined,
  })
  return (
    <div className="space-y-2">
      <p className="text-xs">{count} changed files</p>
      {isLoading && <p>Loading changes…</p>}
      {error && <p>Unable to load changes.</p>}
      <div className="max-h-64 overflow-auto divide-y rounded-md border">
        {data?.items.map((item, index) => (
          <button
            type="button"
            key={item.source_path}
            aria-pressed={selected === pageIndex * 50 + index}
            className={cn(
              "block w-full px-3 py-2 text-left text-xs hover:bg-muted",
              selected === pageIndex * 50 + index && "bg-muted"
            )}
            onClick={() => setSelected(pageIndex * 50 + index)}
          >
            {item.change_type} · {item.source_path}
          </button>
        ))}
      </div>
      <div className="flex gap-2">
        {pageIndex > 0 && (
          <Button
            size="sm"
            variant="outline"
            onClick={() => {
              setPages(pages.slice(0, -1))
              setSelected(undefined)
            }}
          >
            Previous files
          </Button>
        )}
        {data?.next_cursor && (
          <Button
            size="sm"
            variant="outline"
            onClick={() => {
              setPages([...pages, data.next_cursor ?? undefined])
              setSelected(undefined)
            }}
          >
            Next files
          </Button>
        )}
      </div>
      {selected !== undefined && diffIsLoading && <p>Loading file diff…</p>}
      {diffError && <p>Unable to load file diff.</p>}
      {diff && <UnifiedDiff diff={diff.diff} />}
    </div>
  )
}
