"use client"

import { ChevronRightIcon, LockIcon } from "lucide-react"
import { useState } from "react"
import type { SyncResourceType, WorkspaceSyncExportPreview } from "@/client"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import {
  WORKSPACE_SYNC_RESOURCE_TYPE_META,
  WORKSPACE_SYNC_RESOURCE_TYPE_ORDER,
  WORKSPACE_SYNC_ROOT_TO_RESOURCE_TYPE,
  type WorkspaceSyncResourceTypeMeta,
} from "@/components/workspace-sync/resource-metadata"
import { cn } from "@/lib/utils"

interface ResourceGroup {
  type: SyncResourceType
  meta: WorkspaceSyncResourceTypeMeta
  count: number
  files: string[]
}

/**
 * Returns the total number of resources in an export preview projection.
 */
export function getWorkspaceSyncPreviewResourceTotal(
  preview: WorkspaceSyncExportPreview | undefined
): number | undefined {
  if (!preview) {
    return undefined
  }
  return Object.values(preview.resource_counts).reduce(
    (total, count) => total + count,
    0
  )
}

/**
 * Renders the projection total with singular/plural handling, e.g. "12
 * resources" or "1 resource". Falls back to an em dash when unknown.
 */
export function formatResourceTotal(total: number | undefined): string {
  if (total === undefined) {
    return "—"
  }
  return `${total} ${total === 1 ? "resource" : "resources"}`
}

/**
 * Groups a preview projection's flat file list and per-type counts into
 * ordered, displayable groups, dropping types with nothing to push.
 */
function buildResourceGroups(
  preview: WorkspaceSyncExportPreview
): ResourceGroup[] {
  const filesByType = new Map<SyncResourceType, string[]>()
  for (const file of preview.files) {
    const slashIndex = file.indexOf("/")
    if (slashIndex === -1) {
      continue
    }
    const type = WORKSPACE_SYNC_ROOT_TO_RESOURCE_TYPE[file.slice(0, slashIndex)]
    if (!type) {
      continue
    }
    const list = filesByType.get(type) ?? []
    list.push(file)
    filesByType.set(type, list)
  }

  return WORKSPACE_SYNC_RESOURCE_TYPE_ORDER.map((type) => ({
    type,
    meta: WORKSPACE_SYNC_RESOURCE_TYPE_META[type],
    count: preview.resource_counts[type] ?? 0,
    files: filesByType.get(type) ?? [],
  })).filter((group) => group.count > 0 || group.files.length > 0)
}

interface PushResourceManifestProps {
  preview: WorkspaceSyncExportPreview | undefined
  isLoading: boolean
}

/**
 * Enumerates exactly which resources land in the pull request, grouped by type
 * with expandable file paths. Reads the preview projection's per-type counts
 * and file list: the dependency closure that will actually be committed.
 */
export function PushResourceManifest({
  preview,
  isLoading,
}: PushResourceManifestProps) {
  const [expanded, setExpanded] = useState<Set<SyncResourceType>>(
    () => new Set()
  )

  if (!preview) {
    if (!isLoading) {
      return null
    }
    return (
      <div className="flex flex-col gap-2 rounded-lg border p-3">
        <Skeleton className="h-4 w-32 rounded-sm" />
        <Skeleton className="h-4 w-full rounded-sm" />
        <Skeleton className="h-4 w-2/3 rounded-sm" />
      </div>
    )
  }

  const groups = buildResourceGroups(preview)
  if (groups.length === 0) {
    return (
      <div className="rounded-lg border bg-muted/30 p-3 text-xs text-muted-foreground">
        No resources to push.
      </div>
    )
  }

  const fileCount = preview.files.length
  const hasSensitive = groups.some(
    (group) => group.type === "secret_metadata" || group.type === "variable"
  )

  function toggle(type: SyncResourceType) {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(type)) {
        next.delete(type)
      } else {
        next.add(type)
      }
      return next
    })
  }

  return (
    <div className="overflow-hidden rounded-lg border">
      <div className="flex items-center justify-between border-b bg-muted/40 px-3 py-2">
        <span className="text-xs font-semibold">Included in this push</span>
        <span className="font-mono text-xs text-muted-foreground">
          {fileCount} {fileCount === 1 ? "file" : "files"}
        </span>
      </div>
      <ul className="max-h-56 divide-y overflow-y-auto">
        {groups.map((group) => {
          const isOpen = expanded.has(group.type)
          const hasFiles = group.files.length > 0
          return (
            <li key={group.type}>
              <button
                type="button"
                onClick={() => {
                  if (hasFiles) {
                    toggle(group.type)
                  }
                }}
                disabled={!hasFiles}
                aria-expanded={hasFiles ? isOpen : undefined}
                className={cn(
                  "flex w-full items-center gap-2.5 px-3 py-2 text-left",
                  hasFiles && "hover:bg-accent/50"
                )}
              >
                <span className="flex size-6 shrink-0 items-center justify-center rounded bg-muted font-mono text-[9px] font-semibold text-muted-foreground">
                  {group.meta.abbr}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium">
                    {group.meta.label}
                  </span>
                  <span className="block truncate text-[11px] text-muted-foreground">
                    {group.meta.summary}
                  </span>
                </span>
                <Badge
                  variant="secondary"
                  className="h-5 shrink-0 rounded px-1.5 font-mono text-[11px] font-medium"
                >
                  {group.count}
                </Badge>
                {hasFiles ? (
                  <ChevronRightIcon
                    className={cn(
                      "size-3.5 shrink-0 text-muted-foreground transition-transform",
                      isOpen && "rotate-90"
                    )}
                  />
                ) : (
                  <span className="size-3.5 shrink-0" />
                )}
              </button>
              {isOpen && hasFiles ? (
                <ul className="mb-2 ml-[34px] flex flex-col gap-1 border-l-2 border-border py-1 pl-3">
                  {group.files.map((file) => (
                    <li
                      key={file}
                      className="truncate font-mono text-[11px] text-muted-foreground"
                    >
                      {file}
                    </li>
                  ))}
                </ul>
              ) : null}
            </li>
          )
        })}
      </ul>
      {hasSensitive ? (
        <div className="flex items-start gap-2 border-t bg-muted/20 px-3 py-2 text-[11px] leading-relaxed text-muted-foreground">
          <LockIcon className="mt-0.5 size-3 shrink-0" />
          <span>
            Secret and variable values are never committed; only key names and
            metadata.
          </span>
        </div>
      ) : null}
    </div>
  )
}
