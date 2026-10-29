"use client"

import { ChevronRightIcon, PackageIcon } from "lucide-react"
import { useState } from "react"
import type { WorkspacePackageRead, WorkspacePackageResource } from "@/client"
import { Checkbox } from "@/components/ui/checkbox"
import { GitSyncNotice } from "@/components/workspace-sync/git-sync-change-list"
import {
  getWorkspaceSyncResourceAbbr,
  getWorkspaceSyncResourceLabel,
  WORKSPACE_SYNC_RESOURCE_TYPE_ORDER,
} from "@/components/workspace-sync/resource-metadata"
import { cn } from "@/lib/utils"

/** Repository-root file that lists the package's include patterns. */
export const PACKAGE_INCLUDE_FILENAME = ".tracecatinclude"

/** Stable identity of one package resource, e.g. `workflow:wf-triage`. */
function getPackageResourceKey(resource: WorkspacePackageResource): string {
  return `${resource.resource_type}:${resource.source_id}`
}

/**
 * Whether a hand-written pattern (such as `workflows/**`) selects the
 * resource rather than its own line. Unchecking it could not remove it from
 * the package, so the picker locks it.
 */
function isSelectedByOtherPattern(
  resource: WorkspacePackageResource,
  patterns: Set<string>
): boolean {
  return resource.status === "explicit" && !patterns.has(resource.pattern)
}

/**
 * Returns the resources the include file selects by their own line: the
 * picker's initial, editable selection.
 */
function getInitialPackageSelection(
  workspacePackage: WorkspacePackageRead
): Set<string> {
  const patterns = new Set(workspacePackage.patterns ?? [])
  const selected = new Set<string>()
  for (const resource of workspacePackage.resources ?? []) {
    if (resource.status === "explicit" && patterns.has(resource.pattern)) {
      selected.add(getPackageResourceKey(resource))
    }
  }
  return selected
}

/**
 * Builds the include patterns to write for a selection.
 *
 * Existing lines stay in file order. A line that is one resource's own
 * pattern is kept only while that resource is checked; any other line (a
 * glob, a negation, a pattern for a resource that no longer exists) is kept
 * as written. Newly checked resources add their own pattern at the end.
 *
 * Example: patterns `["workflows/a/**", "!tables/x/**"]` with `a` unchecked
 * and `b` checked give `["!tables/x/**", "workflows/b/**"]`.
 */
export function buildPackageInclude(
  workspacePackage: WorkspacePackageRead,
  selected: Set<string>
): string[] {
  const resources = workspacePackage.resources ?? []
  const ownerByPattern = new Map<string, string>()
  for (const resource of resources) {
    ownerByPattern.set(resource.pattern, getPackageResourceKey(resource))
  }
  const include: string[] = []
  const seen = new Set<string>()
  function add(pattern: string) {
    if (!seen.has(pattern)) {
      seen.add(pattern)
      include.push(pattern)
    }
  }
  for (const pattern of workspacePackage.patterns ?? []) {
    const owner = ownerByPattern.get(pattern)
    if (owner === undefined || selected.has(owner)) {
      add(pattern)
    }
  }
  for (const resource of resources) {
    if (selected.has(getPackageResourceKey(resource))) {
      add(resource.pattern)
    }
  }
  return include
}

function setsEqual(left: Set<string>, right: Set<string>): boolean {
  if (left.size !== right.size) {
    return false
  }
  for (const value of left) {
    if (!right.has(value)) {
      return false
    }
  }
  return true
}

/**
 * Holds the package picker's checked resources for one loaded package.
 *
 * The selection resets whenever a different package loads (another branch,
 * or a refetch after a push changed the include file). `packageInclude` is
 * `null` while the selection is unchanged, so a push keeps the include file
 * already on the branch; otherwise it is the full pattern list to write.
 */
export function usePackageSelection(
  workspacePackage: WorkspacePackageRead | undefined
) {
  const [edit, setEdit] = useState<{
    source: WorkspacePackageRead
    selected: Set<string>
  }>()
  const initial = workspacePackage
    ? getInitialPackageSelection(workspacePackage)
    : new Set<string>()
  const selected =
    edit && edit.source === workspacePackage ? edit.selected : initial

  function toggle(resource: WorkspacePackageResource, checked: boolean) {
    if (!workspacePackage) {
      return
    }
    const next = new Set(selected)
    const key = getPackageResourceKey(resource)
    if (checked) {
      next.add(key)
    } else {
      next.delete(key)
    }
    setEdit({ source: workspacePackage, selected: next })
  }

  const isChanged = !setsEqual(selected, initial)
  const packageInclude =
    workspacePackage && isChanged
      ? buildPackageInclude(workspacePackage, selected)
      : null
  return { selected, toggle, isChanged, packageInclude }
}

interface PackageGroup {
  type: string
  resources: WorkspacePackageResource[]
}

function groupPackageResources(
  resources: WorkspacePackageResource[]
): PackageGroup[] {
  const byType = new Map<string, WorkspacePackageResource[]>()
  for (const resource of resources) {
    const list = byType.get(resource.resource_type) ?? []
    list.push(resource)
    byType.set(resource.resource_type, list)
  }
  const knownOrder: string[] = WORKSPACE_SYNC_RESOURCE_TYPE_ORDER
  const types = [...byType.keys()].sort((left, right) => {
    const leftIndex = knownOrder.indexOf(left)
    const rightIndex = knownOrder.indexOf(right)
    if (leftIndex === -1 && rightIndex === -1) {
      return left.localeCompare(right)
    }
    if (leftIndex === -1) {
      return 1
    }
    if (rightIndex === -1) {
      return -1
    }
    return leftIndex - rightIndex
  })
  return types.map((type) => ({
    type,
    resources: [...(byType.get(type) ?? [])].sort((left, right) =>
      left.name.localeCompare(right.name)
    ),
  }))
}

interface PackagePickerProps {
  workspacePackage: WorkspacePackageRead
  selected: Set<string>
  isChanged: boolean
  disabled?: boolean
  onToggle: (resource: WorkspacePackageResource, checked: boolean) => void
}

/**
 * Package panel for the managed resources source workspace: every resource
 * grouped by type with a checkbox. Checked resources are named in the include
 * file. Resources they use come along automatically and show as checked but
 * locked.
 */
export function PackagePicker({
  workspacePackage,
  selected,
  isChanged,
  disabled = false,
  onToggle,
}: PackagePickerProps) {
  const [openGroups, setOpenGroups] = useState<Set<string>>(() => new Set())
  const patterns = new Set(workspacePackage.patterns ?? [])
  const resources = workspacePackage.resources ?? []
  const groups = groupPackageResources(resources)
  const warnings = workspacePackage.warnings ?? []
  const ref = workspacePackage.ref ?? "the default branch"

  function isInPackage(resource: WorkspacePackageResource): boolean {
    return (
      selected.has(getPackageResourceKey(resource)) ||
      resource.status === "implied" ||
      isSelectedByOtherPattern(resource, patterns)
    )
  }

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

  const packageCount = resources.filter(isInPackage).length

  return (
    <section aria-label="Package" className="flex flex-col border-b">
      <div className="flex h-9 items-center gap-2 border-b bg-muted/40 px-5 text-xs text-muted-foreground">
        <PackageIcon className="size-3.5 shrink-0" />
        <span className="font-medium text-foreground">Package</span>
        <span aria-hidden>·</span>
        <span className="min-w-0 truncate">
          {isChanged
            ? `Edited from ${PACKAGE_INCLUDE_FILENAME} on ${ref}`
            : `From ${PACKAGE_INCLUDE_FILENAME} on ${ref}`}
        </span>
        <span className="ml-auto shrink-0 font-mono text-[11px]">
          {packageCount} of {resources.length}
        </span>
      </div>
      <p className="px-5 py-2.5 text-xs text-muted-foreground">
        This workspace is the managed resources source, so every push exports
        the package: the checked resources and the resources they use. Every
        other resource file is deleted from the branch, and the selection is
        saved to {PACKAGE_INCLUDE_FILENAME} in the same commit. Dependencies
        below reflect {ref}; preview to see the effect of your edits.
      </p>
      {warnings.map((warning) => (
        <GitSyncNotice key={warning} tone="warning">
          {warning}
        </GitSyncNotice>
      ))}
      {groups.length === 0 ? (
        <p className="border-t px-5 py-4 text-xs text-muted-foreground">
          This workspace has no resources to package.
        </p>
      ) : (
        <ul className="max-h-96 overflow-y-auto border-t">
          {groups.map((group) => {
            const isOpen = openGroups.has(group.type)
            const groupCount = group.resources.filter(isInPackage).length
            const label = getWorkspaceSyncResourceLabel(group.type)
            return (
              <li key={group.type} className="border-b last:border-b-0">
                <button
                  type="button"
                  aria-expanded={isOpen}
                  onClick={() => toggleGroup(group.type)}
                  className="flex h-10 w-full items-center gap-2.5 px-5 text-left hover:bg-muted/40"
                >
                  <ChevronRightIcon
                    className={cn(
                      "size-3.5 shrink-0 text-muted-foreground transition-transform",
                      isOpen && "rotate-90"
                    )}
                  />
                  <span className="flex size-[22px] shrink-0 items-center justify-center rounded bg-muted font-mono text-[9px] font-semibold text-muted-foreground">
                    {getWorkspaceSyncResourceAbbr(group.type)}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-sm font-medium">
                    {label}
                  </span>
                  <span className="shrink-0 font-mono text-[11px] text-muted-foreground">
                    {groupCount} of {group.resources.length}
                  </span>
                </button>
                {isOpen && (
                  <ul>
                    {group.resources.map((resource) => (
                      <PackageResourceRow
                        key={getPackageResourceKey(resource)}
                        resource={resource}
                        checked={isInPackage(resource)}
                        lockedReason={getLockedReason(resource, patterns)}
                        disabled={disabled}
                        onToggle={onToggle}
                      />
                    ))}
                  </ul>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}

/**
 * Why a row cannot be toggled, or undefined when it can. Implied resources
 * follow the resources that use them; glob-selected ones follow their glob.
 */
function getLockedReason(
  resource: WorkspacePackageResource,
  patterns: Set<string>
): string | undefined {
  if (resource.status === "implied") {
    const users = resource.implied_by ?? []
    if (users.length === 0) {
      return "Included because another resource uses it"
    }
    return `Included because ${users.join(", ")} ${users.length === 1 ? "uses" : "use"} it`
  }
  if (isSelectedByOtherPattern(resource, patterns)) {
    return `Selected by a hand-written pattern in ${PACKAGE_INCLUDE_FILENAME}`
  }
  return undefined
}

function PackageResourceRow({
  resource,
  checked,
  lockedReason,
  disabled,
  onToggle,
}: {
  resource: WorkspacePackageResource
  checked: boolean
  lockedReason: string | undefined
  disabled: boolean
  onToggle: (resource: WorkspacePackageResource, checked: boolean) => void
}) {
  const id = `package-resource-${resource.resource_type}-${resource.source_id}`
  return (
    <li className="flex items-start gap-2.5 py-2 pl-[52px] pr-5 hover:bg-muted/20">
      <Checkbox
        id={id}
        className="mt-0.5"
        checked={checked}
        disabled={disabled || lockedReason !== undefined}
        onCheckedChange={(value) => onToggle(resource, value === true)}
      />
      <label htmlFor={id} className="min-w-0 flex-1 cursor-pointer">
        <span className="block truncate text-sm">{resource.name}</span>
        <span className="block truncate font-mono text-[11px] text-muted-foreground">
          {resource.path}
        </span>
        {lockedReason && (
          <span className="block text-[11px] text-muted-foreground">
            {lockedReason}
          </span>
        )}
      </label>
    </li>
  )
}
