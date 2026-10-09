"use client"

import {
  ChevronRightIcon,
  EyeIcon,
  Loader2Icon,
  LockIcon,
  type LucideIcon,
  SearchIcon,
  SquareDotIcon,
  SquareMinusIcon,
  SquarePlusIcon,
} from "lucide-react"
import { type ReactNode, useState } from "react"
import type { PullResourceDiff, SyncResourceType } from "@/client"
import { Button } from "@/components/ui/button"
import {
  WORKSPACE_SYNC_RESOURCE_TYPE_META,
  WORKSPACE_SYNC_RESOURCE_TYPE_ORDER,
} from "@/components/workspace-sync/resource-metadata"
import { UnifiedDiff } from "@/components/workspace-sync/unified-diff"
import { cn } from "@/lib/utils"

type ChangeType = PullResourceDiff["change_type"]

/** A resource in a sync preview, as both push and pull previews list them. */
export interface GitSyncPreviewResource {
  resource_type: string
  source_id: string
  name: string
  path: string
}

interface ChangeItem {
  key: string
  name: string
  path: string
  /** One per changed file; a resource can span several files. */
  diffs: PullResourceDiff[]
  changeType: ChangeType | undefined
}

interface ChangeGroup {
  type: string
  label: string
  abbr: string
  items: ChangeItem[]
}

const CHANGE_META: Record<
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
 * Full-page empty state before the first preview, with the Preview button.
 */
export function GitSyncPreviewEmpty({
  description,
  isLoading,
  disabled,
  onPreview,
}: {
  description: string
  isLoading: boolean
  disabled: boolean
  onPreview: () => void
}) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-2 px-6 py-16 text-center">
      <SearchIcon className="size-5 text-muted-foreground" />
      <p className="text-sm font-medium text-foreground">No preview yet</p>
      <p className="max-w-xs text-xs text-muted-foreground">{description}</p>
      <div className="mt-1">
        <GitSyncPreviewButton
          hasPreview={false}
          isLoading={isLoading}
          disabled={disabled}
          onPreview={onPreview}
        />
      </div>
    </div>
  )
}

/** Runs the preview: "Preview" the first time, then "Preview again". */
export function GitSyncPreviewButton({
  hasPreview,
  isLoading,
  disabled,
  onPreview,
}: {
  hasPreview: boolean
  isLoading: boolean
  disabled: boolean
  onPreview: () => void
}) {
  return (
    <Button
      type="button"
      variant="outline"
      size="sm"
      className="shrink-0 gap-1.5 text-foreground"
      disabled={disabled || isLoading}
      onClick={onPreview}
    >
      {isLoading ? (
        <Loader2Icon className="size-3.5 animate-spin" />
      ) : (
        <EyeIcon className="size-3.5" />
      )}
      {getPreviewLabel({ isLoading, hasPreview })}
    </Button>
  )
}

/**
 * Counts a preview's changes by kind, e.g. "1 new · 2 modified", or null when
 * nothing changed.
 */
export function formatChangeCounts(diffs: PullResourceDiff[]): string | null {
  const counts = { added: 0, modified: 0, deleted: 0 }
  for (const resourceDiffs of groupDiffsByResource(diffs).values()) {
    counts[getResourceChangeType(resourceDiffs)] += 1
  }
  const parts = [
    counts.added && `${counts.added} new`,
    counts.modified && `${counts.modified} modified`,
    counts.deleted && `${counts.deleted} deleted`,
  ].filter(Boolean)
  return parts.length > 0 ? parts.join(" · ") : null
}

/** Number of changed resources, counting a multi-file resource once. */
export function countChangedResources(diffs: PullResourceDiff[]): number {
  return groupDiffsByResource(diffs).size
}

function diffResourceKey(diff: PullResourceDiff): string {
  return `${diff.resource_type}:${diff.source_id}`
}

function groupDiffsByResource(
  diffs: PullResourceDiff[]
): Map<string, PullResourceDiff[]> {
  const byResource = new Map<string, PullResourceDiff[]>()
  for (const diff of diffs) {
    const key = diffResourceKey(diff)
    byResource.set(key, [...(byResource.get(key) ?? []), diff])
  }
  return byResource
}

/** A resource is new or deleted only when every changed file agrees. */
function getResourceChangeType(diffs: PullResourceDiff[]): ChangeType {
  const [first] = diffs
  return diffs.every((diff) => diff.change_type === first.change_type)
    ? first.change_type
    : "modified"
}

/** Full-width strip above the list for a blocked, failed, or stale state. */
export function GitSyncNotice({
  tone,
  children,
}: {
  tone: "warning" | "error"
  children: ReactNode
}) {
  return (
    <div
      role={tone === "error" ? "alert" : "status"}
      className={cn(
        "border-b px-5 py-2.5 text-xs",
        tone === "warning"
          ? "border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-300"
          : "border-destructive/20 bg-destructive/5 text-destructive"
      )}
    >
      {children}
    </div>
  )
}

/**
 * Resources included in a push or pull as full-width rows, grouped by type.
 * Changed rows carry a change icon and open their diff inline; unchanged rows
 * are listed for scope.
 */
export function GitSyncChangeList({
  workspaceId,
  direction,
  summary,
  resources,
  diffs,
  fileCount,
}: {
  workspaceId: string
  direction: "push" | "pull"
  /** Shown after the title, e.g. "1 new · 2 modified, compared with main". */
  summary: string
  resources: GitSyncPreviewResource[]
  diffs: PullResourceDiff[]
  fileCount: number
}) {
  const [openGroups, setOpenGroups] = useState<Set<string>>(() => new Set())
  const [openItem, setOpenItem] = useState<string | null>(null)
  const groups = buildChangeGroups(resources, diffs)
  const hasSensitive = groups.some(
    (group) => group.type === "secret_metadata" || group.type === "variable"
  )

  function toggleGroup(type: string) {
    setOpenGroups((previous) => {
      const next = new Set(previous)
      if (next.has(type)) {
        next.delete(type)
      } else {
        next.add(type)
      }
      return next
    })
  }

  return (
    <div className="flex flex-col">
      <div className="flex h-9 items-center gap-2 border-b bg-muted/40 px-5 text-xs text-muted-foreground">
        <span className="font-medium text-foreground">
          Included in this {direction}
        </span>
        <span aria-hidden>·</span>
        <span className="min-w-0 truncate">{summary}</span>
        <span className="ml-auto shrink-0 font-mono text-[11px]">
          {fileCount} {fileCount === 1 ? "file" : "files"}
        </span>
      </div>
      {groups.length === 0 ? (
        <p className="px-5 py-6 text-xs text-muted-foreground">
          Nothing to {direction}.
        </p>
      ) : (
        <ul>
          {groups.map((group) => {
            const isOpen = openGroups.has(group.type)
            return (
              <li key={group.type} className="border-b">
                <button
                  type="button"
                  aria-expanded={isOpen}
                  onClick={() => toggleGroup(group.type)}
                  className="flex h-11 w-full items-center gap-2.5 px-5 text-left hover:bg-muted/40"
                >
                  <ChevronRightIcon
                    className={cn(
                      "size-3.5 shrink-0 text-muted-foreground transition-transform",
                      isOpen && "rotate-90"
                    )}
                  />
                  <span className="flex size-[22px] shrink-0 items-center justify-center rounded bg-muted font-mono text-[9px] font-semibold text-muted-foreground">
                    {group.abbr}
                  </span>
                  <span className="w-40 shrink-0 truncate text-sm font-medium">
                    {group.label}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">
                    {formatGroupSummary(group.items)}
                  </span>
                  <span className="shrink-0 font-mono text-[11px] text-muted-foreground">
                    {group.items.length}
                  </span>
                </button>
                {isOpen && (
                  <ul>
                    {group.items.map((item) => (
                      <ChangeRow
                        key={item.key}
                        item={item}
                        isOpen={openItem === item.key}
                        onToggle={() =>
                          setOpenItem((current) =>
                            current === item.key ? null : item.key
                          )
                        }
                      />
                    ))}
                  </ul>
                )}
              </li>
            )
          })}
        </ul>
      )}
      {hasSensitive && (
        <div className="flex items-start gap-2 border-b px-5 py-2.5 text-[11px] leading-relaxed text-muted-foreground">
          <LockIcon className="mt-0.5 size-3 shrink-0" />
          <span>
            Secret and variable values never go through Git. Only key names and
            metadata do.
          </span>
        </div>
      )}
    </div>
  )
}

function ChangeRow({
  item,
  isOpen,
  onToggle,
}: {
  item: ChangeItem
  isOpen: boolean
  onToggle: () => void
}) {
  const { diffs } = item
  const content = (
    <>
      <ChangeIcon changeType={item.changeType} />
      <span
        className={cn(
          "w-56 shrink-0 truncate text-sm",
          diffs.length === 0 && "text-muted-foreground"
        )}
      >
        {item.name}
      </span>
      <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-muted-foreground">
        {item.path}
      </span>
    </>
  )
  const rowClassName = "flex h-10 w-full items-center gap-2.5 pl-[74px] pr-5"

  if (diffs.length === 0) {
    return (
      <li className={cn(rowClassName, "border-t border-border/60")}>
        {content}
        <span className="shrink-0 text-xs text-muted-foreground/70">
          Unchanged
        </span>
      </li>
    )
  }

  return (
    <li className="flex flex-col border-t border-border/60">
      <button
        type="button"
        aria-expanded={isOpen}
        onClick={onToggle}
        className={cn(
          rowClassName,
          "text-left hover:bg-muted/40",
          isOpen && "bg-muted/40"
        )}
      >
        {content}
        <span className="shrink-0 text-xs text-muted-foreground">
          {isOpen ? "Hide diff" : "View diff"}
        </span>
      </button>
      {isOpen &&
        diffs.map((diff) => (
          <FileDiff
            key={diff.source_path}
            diff={diff}
            showPath={diffs.length > 1}
          />
        ))}
    </li>
  )
}

function FileDiff({
  diff,
  showPath,
}: {
  diff: PullResourceDiff
  showPath: boolean
}) {
  const { additions, deletions } = countDiffLines(diff.diff)
  const lineTotal = additions + deletions
  return (
    <div className="mb-2.5 ml-[74px] mr-5 overflow-hidden rounded-md border">
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
        <span className="text-muted-foreground">
          {lineTotal === 1 ? "line" : "lines"}
        </span>
      </div>
      <div className="max-h-80 overflow-auto">
        <UnifiedDiff diff={diff.diff} />
      </div>
      {diff.truncated && (
        <p className="border-t px-2.5 py-1 text-[11px] text-muted-foreground">
          Diff truncated for preview.
        </p>
      )}
    </div>
  )
}

function ChangeIcon({ changeType }: { changeType: ChangeType | undefined }) {
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
 * Joins preview resources to their diffs and groups them by resource type in
 * projection order. Deleted resources only appear as diffs, so unmatched diffs
 * become rows too.
 */
function buildChangeGroups(
  resources: GitSyncPreviewResource[],
  diffs: PullResourceDiff[]
): ChangeGroup[] {
  const diffsByKey = groupDiffsByResource(diffs)

  const itemsByType = new Map<string, ChangeItem[]>()
  const seen = new Set<string>()
  function add(type: string, item: ChangeItem) {
    const list = itemsByType.get(type) ?? []
    list.push(item)
    itemsByType.set(type, list)
    seen.add(item.key)
  }

  for (const resource of resources) {
    const key = `${resource.resource_type}:${resource.source_id}`
    if (seen.has(key)) {
      continue
    }
    const resourceDiffs = diffsByKey.get(key) ?? []
    add(resource.resource_type, {
      key,
      name: resource.name,
      path: resource.path,
      diffs: resourceDiffs,
      changeType:
        resourceDiffs.length > 0
          ? getResourceChangeType(resourceDiffs)
          : undefined,
    })
  }
  for (const [key, resourceDiffs] of diffsByKey) {
    if (seen.has(key)) {
      continue
    }
    const [first] = resourceDiffs
    add(first.resource_type, {
      key,
      name: first.title ?? first.source_id,
      path: first.source_path,
      diffs: resourceDiffs,
      changeType: getResourceChangeType(resourceDiffs),
    })
  }

  const knownTypes: string[] = WORKSPACE_SYNC_RESOURCE_TYPE_ORDER
  const unknownTypes = [...itemsByType.keys()]
    .filter((type) => !knownTypes.includes(type))
    .sort()
  return [...knownTypes, ...unknownTypes]
    .filter((type) => itemsByType.has(type))
    .map((type) => {
      const meta = isKnownType(type)
        ? WORKSPACE_SYNC_RESOURCE_TYPE_META[type]
        : undefined
      return {
        type,
        label: meta?.label ?? type,
        abbr: meta?.abbr ?? type.slice(0, 2).toUpperCase(),
        items: (itemsByType.get(type) ?? []).sort(compareItems),
      }
    })
}

/** Changed rows first (new, modified, deleted), then by name. */
function compareItems(left: ChangeItem, right: ChangeItem): number {
  const leftOrder = left.changeType ? CHANGE_META[left.changeType].order : 3
  const rightOrder = right.changeType ? CHANGE_META[right.changeType].order : 3
  if (leftOrder !== rightOrder) {
    return leftOrder - rightOrder
  }
  return left.name.localeCompare(right.name)
}

function formatGroupSummary(items: ChangeItem[]): string {
  const counts = { added: 0, modified: 0, deleted: 0, unchanged: 0 }
  for (const item of items) {
    if (item.changeType) {
      counts[item.changeType] += 1
    } else {
      counts.unchanged += 1
    }
  }
  return [
    counts.added && `${counts.added} new`,
    counts.modified && `${counts.modified} modified`,
    counts.deleted && `${counts.deleted} deleted`,
    counts.unchanged && `${counts.unchanged} unchanged`,
  ]
    .filter(Boolean)
    .join(" · ")
}

/** Counts added and removed lines in a unified diff, skipping file headers. */
function countDiffLines(diff: string): {
  additions: number
  deletions: number
} {
  let additions = 0
  let deletions = 0
  for (const line of diff.split("\n")) {
    if (line.startsWith("+++ ") || line.startsWith("--- ")) {
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

function isKnownType(type: string): type is SyncResourceType {
  return Object.prototype.hasOwnProperty.call(
    WORKSPACE_SYNC_RESOURCE_TYPE_META,
    type
  )
}

function getPreviewLabel({
  isLoading,
  hasPreview,
}: {
  isLoading: boolean
  hasPreview: boolean
}): string {
  if (isLoading) {
    return "Previewing..."
  }
  if (hasPreview) {
    return "Preview again"
  }
  return "Preview"
}
