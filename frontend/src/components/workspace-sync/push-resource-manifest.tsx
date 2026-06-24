"use client"

import { ChevronRightIcon, LockIcon } from "lucide-react"
import { useState } from "react"
import type {
  PullResourceDiff,
  PullResult,
  ResourcePullCount,
  SyncPreviewResource,
  SyncResourceType,
  WorkspaceSyncExportPreview,
  WorkspaceSyncPreviewResource,
} from "@/client"
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
  resources: ResourceManifestResource[]
  files: string[]
}

type ResourceManifestResource =
  | WorkspaceSyncPreviewResource
  | SyncPreviewResource

type ResourceCountValue = number | ResourcePullCount

interface ResourceManifestPreview {
  resource_counts: Record<string, ResourceCountValue | undefined>
  files: string[]
  resources?: ResourceManifestResource[] | null
}

/**
 * Returns the total number of resources in a sync preview projection.
 */
export function getWorkspaceSyncPreviewResourceTotal(
  preview: ResourceManifestPreview | undefined
): number | undefined {
  if (!preview) {
    return undefined
  }
  let total = 0
  for (const count of Object.values(preview.resource_counts)) {
    total += getResourceCount(count) ?? 0
  }
  return total
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
 * ordered, displayable groups.
 */
function buildResourceGroups(
  preview: ResourceManifestPreview
): ResourceGroup[] {
  const resourcesByType = new Map<
    SyncResourceType,
    ResourceManifestResource[]
  >()
  for (const resource of preview.resources ?? []) {
    const resourceType = toSyncResourceType(resource.resource_type)
    if (!resourceType) {
      continue
    }
    const list = resourcesByType.get(resourceType) ?? []
    list.push(resource)
    resourcesByType.set(resourceType, list)
  }

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

  return WORKSPACE_SYNC_RESOURCE_TYPE_ORDER.map((type) => {
    const resources = resourcesByType.get(type) ?? []
    return {
      type,
      meta: WORKSPACE_SYNC_RESOURCE_TYPE_META[type],
      count:
        getResourceCount(preview.resource_counts[type]) ?? resources.length,
      resources,
      files: filesByType.get(type) ?? [],
    }
  }).filter(
    (group) =>
      group.count > 0 || group.resources.length > 0 || group.files.length > 0
  )
}

interface PushResourceManifestProps {
  preview: WorkspaceSyncExportPreview | undefined
  isLoading: boolean
  errorMessage?: string
}

/**
 * Enumerates exactly which resources land in a push preview, grouped by type
 * with expandable file paths.
 */
export function PushResourceManifest({
  preview,
  isLoading,
  errorMessage,
}: PushResourceManifestProps) {
  return (
    <WorkspaceSyncResourceManifest
      preview={preview}
      isLoading={isLoading}
      errorMessage={errorMessage}
      direction="push"
    />
  )
}

/**
 * Enumerates resources found by a dry-run pull before the file-level diff.
 */
export function PullResourceManifest({ result }: { result: PullResult }) {
  return (
    <WorkspaceSyncResourceManifest
      preview={pullResultToResourceManifest(result)}
      isLoading={false}
      direction="pull"
    />
  )
}

function WorkspaceSyncResourceManifest({
  preview,
  isLoading,
  errorMessage,
  direction,
}: {
  preview: ResourceManifestPreview | undefined
  isLoading: boolean
  errorMessage?: string
  direction: "push" | "pull"
}) {
  const [expanded, setExpanded] = useState<Set<SyncResourceType>>(
    () => new Set()
  )

  if (!preview) {
    if (errorMessage) {
      return (
        <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-xs text-destructive">
          Unable to preview this {direction}: {errorMessage}
        </div>
      )
    }
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
        No resources to {direction}.
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
        <span className="text-xs font-semibold">
          Included in this {direction}
        </span>
        <span className="font-mono text-xs text-muted-foreground">
          {fileCount} {fileCount === 1 ? "file" : "files"}
        </span>
      </div>
      <ul className="max-h-56 divide-y overflow-y-auto">
        {groups.map((group) => {
          const isOpen = expanded.has(group.type)
          const hasFiles = group.files.length > 0
          const hasDetails = hasFiles || group.resources.length > 0
          return (
            <li key={group.type}>
              <button
                type="button"
                onClick={() => {
                  if (hasDetails) {
                    toggle(group.type)
                  }
                }}
                disabled={!hasDetails}
                aria-expanded={hasDetails ? isOpen : undefined}
                className={cn(
                  "flex w-full items-center gap-2.5 px-3 py-2 text-left",
                  hasDetails && "hover:bg-accent/50"
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
                    {formatGroupSummary(group)}
                  </span>
                </span>
                <Badge
                  variant="secondary"
                  className="h-5 shrink-0 rounded px-1.5 font-mono text-[11px] font-medium"
                >
                  {group.count}
                </Badge>
                {hasDetails ? (
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
              {isOpen && hasDetails ? (
                <ul className="mb-2 ml-[34px] flex flex-col gap-1 border-l-2 border-border py-1 pl-3">
                  {group.resources.length > 0
                    ? group.resources.map((resource) => (
                        <li
                          key={`${resource.resource_type}:${resource.source_id}`}
                          className="min-w-0"
                        >
                          <span className="block truncate text-xs font-medium">
                            {resource.name}
                          </span>
                          <span className="block truncate font-mono text-[11px] text-muted-foreground">
                            {resource.path}
                          </span>
                        </li>
                      ))
                    : group.files.map((file) => (
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
            Secret and variable values are never synced through Git; only key
            names and metadata are included.
          </span>
        </div>
      ) : null}
    </div>
  )
}

function formatGroupSummary(group: ResourceGroup): string {
  const names = group.resources.map((resource) => resource.name)
  if (names.length === 0) {
    return group.meta.summary
  }

  const visibleNames = names.slice(0, 3).join(", ")
  const remaining = names.length - 3
  if (remaining <= 0) {
    return visibleNames
  }
  return `${visibleNames} +${remaining} more`
}

function pullResultToResourceManifest(
  result: PullResult
): ResourceManifestPreview {
  return {
    resource_counts: result.resource_counts ?? {},
    files:
      result.files ??
      (result.resource_diffs ?? []).map((diff) => diff.source_path),
    resources:
      result.resources ?? resourcesFromDiffs(result.resource_diffs ?? []),
  }
}

function resourcesFromDiffs(
  resourceDiffs: PullResourceDiff[]
): ResourceManifestResource[] {
  return resourceDiffs.map((diff) => ({
    resource_type: diff.resource_type,
    source_id: diff.source_id,
    name: diff.title ?? diff.source_id,
    path: diff.source_path,
  }))
}

function getResourceCount(
  count: ResourceCountValue | undefined
): number | undefined {
  if (count === undefined) {
    return undefined
  }
  if (typeof count === "number") {
    return count
  }
  return count.found
}

const WORKSPACE_SYNC_RESOURCE_TYPE_SET = new Set<string>(
  WORKSPACE_SYNC_RESOURCE_TYPE_ORDER
)

function toSyncResourceType(resourceType: string): SyncResourceType | null {
  if (!WORKSPACE_SYNC_RESOURCE_TYPE_SET.has(resourceType)) {
    return null
  }
  return resourceType as SyncResourceType
}
