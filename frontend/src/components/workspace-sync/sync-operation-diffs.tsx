"use client"

import {
  Loader2Icon,
  type LucideIcon,
  SquareDotIcon,
  SquareMinusIcon,
  SquarePlusIcon,
} from "lucide-react"
import { useState } from "react"
import {
  type PullResourceDiff,
  workflowsGetSyncDiff,
  workflowsListSyncDiffs,
} from "@/client"
import { Button } from "@/components/ui/button"
import { UnifiedDiff } from "@/components/workspace-sync/unified-diff"
import { useInfiniteQuery, useQuery } from "@/lib/query"
import { cn } from "@/lib/utils"

type ChangeType = PullResourceDiff["change_type"]

/** A diff from a persisted operation, with its position for loading contents. */
export interface OperationDiff extends PullResourceDiff {
  index?: number
}

/** Identifies the persisted operation a diff's contents load from. */
export interface OperationDiffSource {
  workspaceId: string
  operationId: string
}

export const CHANGE_META: Record<
  ChangeType,
  { label: string; icon: LucideIcon; className: string; order: number }
> = {
  added: {
    label: "New",
    icon: SquarePlusIcon,
    className: "text-green-700 dark:text-green-500",
    order: 0,
  },
  modified: {
    label: "Modified",
    icon: SquareDotIcon,
    className: "text-amber-700 dark:text-amber-500",
    order: 1,
  },
  deleted: {
    label: "Deleted",
    icon: SquareMinusIcon,
    className: "text-red-700 dark:text-red-500",
    order: 2,
  },
}

/**
 * Loads an operation's diff metadata one bounded page at a time. File
 * contents are fetched separately, only when a row is opened.
 */
export function useOperationDiffs(
  workspaceId: string,
  operationId: string | undefined
) {
  const query = useInfiniteQuery({
    queryKey: ["sync-operation-diffs", workspaceId, operationId],
    queryFn: ({ pageParam }) =>
      workflowsListSyncDiffs({
        workspaceId,
        operationId: operationId ?? "",
        cursor: pageParam,
      }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    enabled: Boolean(operationId),
  })
  const diffs: OperationDiff[] = []
  for (const page of query.data?.pages ?? []) {
    for (const item of page.items) {
      diffs.push({ ...item, index: diffs.length })
    }
  }
  return {
    diffs,
    isLoading: query.isLoading,
    error: query.error,
    hasMore: Boolean(query.hasNextPage),
    isLoadingMore: query.isFetchingNextPage,
    loadMore: () => void query.fetchNextPage(),
  }
}

/** Change kind icon with an accessible label. */
export function ChangeIcon({
  changeType,
}: {
  changeType: ChangeType | undefined
}) {
  if (!changeType) {
    return <span aria-hidden className="size-[15px] shrink-0" />
  }
  const meta = CHANGE_META[changeType]
  const Icon = meta.icon
  return (
    <span
      role="img"
      aria-label={meta.label}
      title={meta.label}
      className={cn("inline-flex shrink-0", meta.className)}
    >
      <Icon aria-hidden className="size-[15px]" />
    </span>
  )
}

/**
 * One file's diff. Diffs from a persisted operation arrive without contents,
 * so those are loaded when the file is shown.
 */
export function FileDiff({
  diff,
  showPath,
  source,
  className,
}: {
  diff: OperationDiff
  showPath: boolean
  source?: OperationDiffSource
  className?: string
}) {
  const needsContents = diff.diff === "" && diff.index !== undefined && source
  const {
    data: loaded,
    isLoading,
    error,
  } = useQuery({
    queryKey: [
      "sync-operation-diff",
      source?.workspaceId,
      source?.operationId,
      diff.index,
    ],
    queryFn: () =>
      workflowsGetSyncDiff({
        workspaceId: source?.workspaceId ?? "",
        operationId: source?.operationId ?? "",
        index: diff.index ?? 0,
      }),
    enabled: Boolean(needsContents),
  })
  const text = needsContents ? (loaded?.diff ?? "") : diff.diff
  const { additions, deletions } = countDiffLines(text)
  const lineTotal = additions + deletions
  return (
    <div className={cn("overflow-hidden rounded-md border", className)}>
      <div className="flex items-center gap-2 border-b bg-muted/40 px-2.5 py-1 font-mono text-[11px]">
        {showPath && (
          <span className="min-w-0 truncate text-foreground">
            {diff.source_path}
          </span>
        )}
        {additions > 0 && (
          <span className="text-green-700 dark:text-green-500">
            +{additions}
          </span>
        )}
        {deletions > 0 && (
          <span className="text-red-700 dark:text-red-500">−{deletions}</span>
        )}
        {lineTotal > 0 && (
          <span className="text-muted-foreground">
            {lineTotal === 1 ? "line" : "lines"}
          </span>
        )}
      </div>
      {isLoading && (
        <p className="flex items-center gap-2 px-2.5 py-2 text-xs text-muted-foreground">
          <Loader2Icon className="size-3.5 animate-spin" />
          Loading diff...
        </p>
      )}
      {error && (
        <p className="px-2.5 py-2 text-xs text-destructive">
          Could not load this diff. Preview again to refresh it.
        </p>
      )}
      {text && (
        <div className="max-h-80 overflow-auto">
          <UnifiedDiff diff={text} />
        </div>
      )}
      {(needsContents ? loaded?.truncated : diff.truncated) && (
        <p className="border-t px-2.5 py-1 text-[11px] text-muted-foreground">
          Diff truncated for preview.
        </p>
      )}
    </div>
  )
}

/** Loads the next page of an operation's changed files. */
export function LoadMoreDiffsButton({
  isLoading,
  onClick,
}: {
  isLoading: boolean
  onClick: () => void
}) {
  return (
    <Button
      type="button"
      variant="ghost"
      size="sm"
      className="h-8 gap-1.5 text-xs text-muted-foreground"
      disabled={isLoading}
      onClick={onClick}
    >
      {isLoading && <Loader2Icon className="size-3.5 animate-spin" />}
      Show more files
    </Button>
  )
}

/**
 * Changed files of a persisted operation as one readable list. Each row opens
 * its diff in place.
 */
export function OperationDiffs({
  workspaceId,
  operationId,
  count,
}: {
  workspaceId: string
  operationId: string
  count: number
}) {
  return (
    <OperationDiffList
      key={`${workspaceId}/${operationId}`}
      workspaceId={workspaceId}
      operationId={operationId}
      count={count}
    />
  )
}

function OperationDiffList({
  workspaceId,
  operationId,
  count,
}: {
  workspaceId: string
  operationId: string
  count: number
}) {
  const [openIndex, setOpenIndex] = useState<number>()
  const { diffs, isLoading, error, hasMore, isLoadingMore, loadMore } =
    useOperationDiffs(workspaceId, operationId)
  const source = { workspaceId, operationId }
  return (
    <div className="flex flex-col gap-2">
      <p className="text-xs text-muted-foreground">
        {count} changed {count === 1 ? "file" : "files"}
      </p>
      {isLoading && (
        <p className="text-xs text-muted-foreground">Loading changes...</p>
      )}
      {error && (
        <p className="text-xs text-destructive">
          Could not load changes. Preview again to refresh them.
        </p>
      )}
      {diffs.length > 0 && (
        <ul className="divide-y overflow-hidden rounded-md border">
          {diffs.map((diff) => {
            const isOpen = openIndex === diff.index
            return (
              <li key={diff.index} className="flex flex-col">
                <button
                  type="button"
                  aria-expanded={isOpen}
                  className={cn(
                    "flex h-9 w-full items-center gap-2.5 px-3 text-left hover:bg-muted/40",
                    isOpen && "bg-muted/40"
                  )}
                  onClick={() => setOpenIndex(isOpen ? undefined : diff.index)}
                >
                  <ChangeIcon changeType={diff.change_type} />
                  <span className="min-w-0 flex-1 truncate font-mono text-xs">
                    {diff.source_path}
                    {/* Bundled resources such as case tags share one path. */}
                    <span className="font-sans text-muted-foreground">
                      {" "}
                      · {diff.title ?? diff.source_id}
                    </span>
                  </span>
                  <span className="shrink-0 text-xs text-muted-foreground">
                    {isOpen ? "Hide diff" : "View diff"}
                  </span>
                </button>
                {isOpen && (
                  <FileDiff
                    diff={diff}
                    showPath={false}
                    source={source}
                    className="mx-3 mb-2.5"
                  />
                )}
              </li>
            )
          })}
        </ul>
      )}
      {hasMore && (
        <div>
          <LoadMoreDiffsButton isLoading={isLoadingMore} onClick={loadMore} />
        </div>
      )}
    </div>
  )
}

/** Counts added and removed lines in a unified diff, skipping file headers. */
export function countDiffLines(diff: string): {
  additions: number
  deletions: number
} {
  let additions = 0
  let deletions = 0
  // File headers only come before the first hunk; later "+++ " lines are content.
  let inHunk = false
  for (const line of diff.split("\n")) {
    if (line.startsWith("@@")) {
      inHunk = true
      continue
    }
    if (!inHunk) {
      continue
    }
    if (line.startsWith("+")) {
      additions += 1
    } else if (line.startsWith("-")) {
      deletions += 1
    }
  }
  return { additions, deletions }
}
