"use client"

import {
  ChevronRightIcon,
  CircleMinusIcon,
  CirclePlusIcon,
  EyeIcon,
  Loader2Icon,
  type LucideIcon,
  PencilIcon,
} from "lucide-react"
import { useState } from "react"
import type { PullResourceDiff, WorkspaceSyncExportPreview } from "@/client"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { PushResourceManifest } from "@/components/workspace-sync/push-resource-manifest"
import { getWorkspaceSyncResourceLabel } from "@/components/workspace-sync/resource-metadata"
import { UnifiedDiff } from "@/components/workspace-sync/unified-diff"
import { cn } from "@/lib/utils"

interface PushResourcePreviewProps {
  preview: WorkspaceSyncExportPreview | undefined
  isLoading: boolean
  compareRef: string | undefined
  errorMessage?: string
  hasRequestedPreview: boolean
  onRequestPreview: () => void
}

/**
 * Shows the on-demand push preview: a resource manifest first, then file diffs.
 */
export function PushResourcePreview({
  preview,
  isLoading,
  compareRef,
  errorMessage,
  hasRequestedPreview,
  onRequestPreview,
}: PushResourcePreviewProps) {
  const header = (
    <PushPreviewHeader
      compareRef={compareRef}
      isLoading={isLoading}
      hasRequestedPreview={hasRequestedPreview}
      onRequestPreview={onRequestPreview}
    />
  )

  if (!hasRequestedPreview) {
    return <div className="rounded-lg border bg-muted/30 p-3">{header}</div>
  }

  if (!compareRef) {
    return (
      <div className="flex flex-col gap-2 rounded-lg border bg-muted/30 p-3">
        {header}
        <div className="text-xs text-muted-foreground">
          Select a target branch to preview changes.
        </div>
      </div>
    )
  }

  if (isLoading && !preview) {
    return (
      <div className="flex flex-col gap-2 rounded-lg border p-3">
        {header}
        <Skeleton className="h-4 w-32 rounded-sm" />
        <Skeleton className="h-4 w-full rounded-sm" />
        <Skeleton className="h-4 w-2/3 rounded-sm" />
      </div>
    )
  }

  if (!preview) {
    if (errorMessage) {
      return (
        <div className="flex flex-col gap-2">
          {header}
          <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-xs text-destructive">
            Unable to preview changes: {errorMessage}
          </div>
        </div>
      )
    }
    return null
  }

  const resourceDiffs = preview.resource_diffs ?? []
  return (
    <div className="flex flex-col gap-3">
      {header}
      <PushResourceManifest preview={preview} isLoading={false} />
      <ResourceDiffSection diffs={resourceDiffs} emptyRef={compareRef} />
    </div>
  )
}

function PushPreviewHeader({
  compareRef,
  isLoading,
  hasRequestedPreview,
  onRequestPreview,
}: {
  compareRef: string | undefined
  isLoading: boolean
  hasRequestedPreview: boolean
  onRequestPreview: () => void
}) {
  const disabled = !compareRef || isLoading

  return (
    <div className="flex flex-wrap items-center justify-between gap-2">
      <div className="min-w-0">
        <span className="block text-xs font-semibold">Preview</span>
        <span className="block truncate font-mono text-xs text-muted-foreground">
          {compareRef
            ? `changes against ${compareRef}`
            : "Select a target branch first"}
        </span>
      </div>
      <Button
        type="button"
        variant="outline"
        size="sm"
        className="h-7 shrink-0 gap-1.5 text-xs"
        disabled={disabled}
        onClick={onRequestPreview}
      >
        {isLoading ? (
          <Loader2Icon className="size-3.5 animate-spin" />
        ) : (
          <EyeIcon className="size-3.5" />
        )}
        {getPreviewButtonLabel({ hasRequestedPreview, isLoading })}
      </Button>
    </div>
  )
}

function getPreviewButtonLabel({
  hasRequestedPreview,
  isLoading,
}: {
  hasRequestedPreview: boolean
  isLoading: boolean
}): string {
  if (isLoading) {
    return "Previewing..."
  }
  if (hasRequestedPreview) {
    return "Refresh preview"
  }
  return "Preview changes"
}

/**
 * File-level details within a sync preview.
 */
export function ResourceDiffSection({
  diffs,
  emptyRef,
}: {
  diffs: PullResourceDiff[]
  emptyRef?: string
}) {
  return (
    <div className="flex flex-col gap-2">
      <span className="text-xs font-semibold">File changes</span>
      {diffs.length > 0 ? (
        <ResourceDiffReviewList diffs={diffs} />
      ) : (
        <div className="rounded-lg border bg-muted/30 p-3 text-xs text-muted-foreground">
          {emptyRef
            ? `No file changes against ${emptyRef}.`
            : "No file changes detected."}
        </div>
      )}
    </div>
  )
}

interface ResourceDiffReviewListProps {
  diffs: PullResourceDiff[]
}

const CHANGE_TYPE_META: Record<
  PullResourceDiff["change_type"],
  { icon: LucideIcon; label: string; className: string }
> = {
  added: {
    icon: CirclePlusIcon,
    label: "Added",
    className: "text-green-600",
  },
  modified: {
    icon: PencilIcon,
    label: "Modified",
    className: "text-amber-600",
  },
  deleted: {
    icon: CircleMinusIcon,
    label: "Deleted",
    className: "text-destructive",
  },
}

/**
 * Review list for per-resource sync diffs.
 */
export function ResourceDiffReviewList({ diffs }: ResourceDiffReviewListProps) {
  const [viewed, setViewed] = useState<Set<string>>(() => new Set())
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set())

  const viewedCount = diffs.reduce(
    (total, diff) => (viewed.has(diff.source_path) ? total + 1 : total),
    0
  )
  const allViewed = diffs.length > 0 && viewedCount === diffs.length
  const viewedPercent =
    diffs.length === 0 ? 0 : (viewedCount / diffs.length) * 100

  function setItemViewed(sourcePath: string, value: boolean) {
    setViewed((previous) => withMember(previous, sourcePath, value))
    setCollapsed((previous) => withMember(previous, sourcePath, value))
  }

  function setItemOpen(sourcePath: string, open: boolean) {
    setCollapsed((previous) => withMember(previous, sourcePath, !open))
  }

  function markAllViewed() {
    const paths = diffs.map((diff) => diff.source_path)
    setViewed(new Set(paths))
    setCollapsed(new Set(paths))
  }

  return (
    <TooltipProvider delayDuration={200}>
      <div className="overflow-hidden rounded-md border">
        <div className="flex flex-wrap items-center gap-3 border-b bg-muted/50 px-3 py-2">
          <span className="text-xs font-medium tabular-nums">
            {viewedCount} of {diffs.length}{" "}
            <span className="font-normal text-muted-foreground">viewed</span>
          </span>
          <div className="h-1.5 min-w-[80px] flex-1 overflow-hidden rounded-full bg-border">
            <div
              className="h-full rounded-full bg-secondary-foreground/40 transition-all"
              style={{ width: `${viewedPercent}%` }}
            />
          </div>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="h-7 text-xs"
            disabled={allViewed}
            onClick={markAllViewed}
          >
            Viewed all
          </Button>
        </div>

        <div className="divide-y">
          {diffs.map((diff) => (
            <ResourceDiffItem
              key={diff.source_path}
              diff={diff}
              viewed={viewed.has(diff.source_path)}
              open={!collapsed.has(diff.source_path)}
              onViewedChange={(value) => setItemViewed(diff.source_path, value)}
              onOpenChange={(open) => setItemOpen(diff.source_path, open)}
            />
          ))}
        </div>
      </div>
    </TooltipProvider>
  )
}

/**
 * Single changed resource file in a sync preview.
 */
function ResourceDiffItem({
  diff,
  viewed,
  open,
  onViewedChange,
  onOpenChange,
}: {
  diff: PullResourceDiff
  viewed: boolean
  open: boolean
  onViewedChange: (value: boolean) => void
  onOpenChange: (open: boolean) => void
}) {
  return (
    <Collapsible open={open} onOpenChange={onOpenChange}>
      <div className="flex items-center gap-2 px-3 py-2.5">
        <CollapsibleTrigger className="flex min-w-0 flex-1 items-center gap-2 text-left">
          <ChevronRightIcon
            className={cn(
              "size-3.5 shrink-0 text-muted-foreground transition-transform",
              open && "rotate-90"
            )}
          />
          <ResourceChangeIcon changeType={diff.change_type} />
          <span
            className={cn(
              "min-w-0 truncate text-sm font-medium",
              viewed && "text-muted-foreground"
            )}
          >
            {diff.title ?? diff.source_id}
          </span>
          <span className="shrink-0 font-mono text-[11px] text-muted-foreground">
            {getWorkspaceSyncResourceLabel(diff.resource_type)}
          </span>
        </CollapsibleTrigger>
        <div className="flex shrink-0 items-center gap-1.5">
          <Checkbox
            checked={viewed}
            onCheckedChange={(value) => onViewedChange(value === true)}
            aria-label="Mark as viewed"
            className="border-input shadow-none data-[state=checked]:border-muted-foreground data-[state=checked]:bg-secondary data-[state=checked]:text-secondary-foreground"
          />
          <button
            type="button"
            onClick={() => onViewedChange(!viewed)}
            className="text-xs text-muted-foreground transition-colors hover:text-foreground"
          >
            Viewed
          </button>
        </div>
      </div>
      <CollapsibleContent>
        <div className="flex flex-col gap-2 px-3 pb-3">
          <div className="font-mono text-[11px] text-muted-foreground">
            {diff.source_path}
          </div>
          <div className="max-h-96 min-w-0 overflow-auto rounded-md border bg-background">
            <UnifiedDiff diff={diff.diff} />
          </div>
          {diff.truncated && (
            <p className="text-[11px] text-muted-foreground">
              Diff truncated for preview.
            </p>
          )}
        </div>
      </CollapsibleContent>
    </Collapsible>
  )
}

function ResourceChangeIcon({
  changeType,
}: {
  changeType: PullResourceDiff["change_type"]
}) {
  const meta = CHANGE_TYPE_META[changeType]
  const Icon = meta.icon

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          aria-label={meta.label}
          className={cn(
            "inline-flex size-5 shrink-0 items-center justify-center",
            meta.className
          )}
        >
          <Icon aria-hidden className="size-3.5" />
        </span>
      </TooltipTrigger>
      <TooltipContent>{meta.label}</TooltipContent>
    </Tooltip>
  )
}

function withMember<T>(set: Set<T>, member: T, include: boolean): Set<T> {
  const next = new Set(set)
  if (include) {
    next.add(member)
  } else {
    next.delete(member)
  }
  return next
}
