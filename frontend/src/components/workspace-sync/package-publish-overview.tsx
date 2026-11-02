"use client"

import type { PullResourceDiff } from "@/client"
import { getWorkspaceSyncResourceLabel } from "@/components/workspace-sync/resource-metadata"

/** Path of the platform admin page where releases are cut and deployed. */
export const MANAGED_PACKAGE_ADMIN_PATH = "/admin/managed-package"

const CHANGE_GROUPS = [
  { change: "added", label: "Added to the package" },
  { change: "modified", label: "Updated in the package" },
  { change: "deleted", label: "Removed from the package" },
] as const

/** One changed resource, summarised over every file it changed. */
interface ResourceChange {
  key: string
  resourceType: string
  label: string
  change: PullResourceDiff["change_type"]
}

/**
 * Collapse per-file diffs into one entry per resource. A resource whose files
 * were all added is added, all deleted is deleted, and anything else is
 * updated.
 *
 * Example: a workflow with `definition.yml` modified and `layout.yml` added
 * gives one "modified" entry.
 */
function groupDiffsByResource(diffs: PullResourceDiff[]): ResourceChange[] {
  const byKey = new Map<string, ResourceChange>()
  for (const diff of diffs) {
    const key = `${diff.resource_type}:${diff.source_id}`
    const existing = byKey.get(key)
    if (!existing) {
      byKey.set(key, {
        key,
        resourceType: diff.resource_type,
        label: diff.title ?? diff.source_id,
        change: diff.change_type,
      })
      continue
    }
    // Deletions carry no title, so keep the first title seen.
    if (diff.title && existing.label === diff.source_id) {
      existing.label = diff.title
    }
    if (existing.change !== diff.change_type) {
      existing.change = "modified"
    }
  }
  return [...byKey.values()]
}

/**
 * Lists, by name, what publishing adds to, updates in, and removes from the
 * package, compared with the branch.
 */
export function PackagePublishSummary({
  diffs,
  compareRef,
}: {
  diffs: PullResourceDiff[]
  compareRef: string | undefined
}) {
  const changes = groupDiffsByResource(diffs)
  const groups = CHANGE_GROUPS.map((group) => ({
    ...group,
    items: changes.filter((change) => change.change === group.change),
  })).filter((group) => group.items.length > 0)

  return (
    <div className="space-y-2 border-b px-5 py-3 text-xs">
      <p className="font-medium">
        What this publish changes, compared with {compareRef ?? "the branch"}
      </p>
      {groups.length === 0 ? (
        <p className="text-muted-foreground">
          Nothing. The package on the branch already matches your selection.
        </p>
      ) : (
        groups.map((group) => (
          <div key={group.change} className="space-y-1">
            <p className="text-muted-foreground">
              {group.label} ({group.items.length})
            </p>
            <ul className="flex flex-wrap gap-1.5">
              {group.items.map((change) => (
                <li key={change.key} className="rounded border px-1.5 py-0.5">
                  <span className="text-muted-foreground">
                    {getWorkspaceSyncResourceLabel(change.resourceType)}:
                  </span>{" "}
                  {change.label}
                </li>
              ))}
            </ul>
          </div>
        ))
      )}
    </div>
  )
}
