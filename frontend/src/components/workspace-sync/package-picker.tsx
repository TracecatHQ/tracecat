"use client"

import { ChevronRightIcon, PackageIcon, SearchIcon } from "lucide-react"
import Link from "next/link"
import type { ReactNode } from "react"
import { useState } from "react"
import type { WorkspacePackageRead, WorkspacePackageResource } from "@/client"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import {
  getWorkspaceSyncResourceLabel,
  WORKSPACE_SYNC_RESOURCE_TYPE_ORDER,
} from "@/components/workspace-sync/resource-metadata"
import { useAuth } from "@/hooks/use-auth"
import { cn } from "@/lib/utils"

/** Repository-root file that lists the package's include patterns. */
export const PACKAGE_INCLUDE_FILENAME = ".tracecatinclude"

/** Platform admin page where releases are cut and deployed. */
const MANAGED_PACKAGE_ADMIN_PATH = "/admin/managed-package"
const MANAGED_PACKAGE_ADMIN_LABEL = "Admin, Managed Workspaces"

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

  function setSelected(next: Set<string>) {
    if (workspacePackage) {
      setEdit({ source: workspacePackage, selected: next })
    }
  }

  const isChanged = !setsEqual(selected, initial)
  const packageInclude =
    workspacePackage && isChanged
      ? buildPackageInclude(workspacePackage, selected)
      : null
  return { selected, setSelected, isChanged, packageInclude }
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

/**
 * Works out each resource's state under a selection.
 *
 * An implied resource stays only while a resource that uses it is still in
 * the package, so unchecking its last user drops it too.
 */
function getPackageMembership(
  workspacePackage: WorkspacePackageRead,
  selected: Set<string>
) {
  const patterns = new Set(workspacePackage.patterns ?? [])
  const resources = workspacePackage.resources ?? []
  // Names of the explicit resources still in the package after edits.
  const rootNames = new Set(
    resources
      .filter(
        (resource) =>
          selected.has(getPackageResourceKey(resource)) ||
          isSelectedByOtherPattern(resource, patterns)
      )
      .map((resource) => resource.name)
  )

  function isImplied(resource: WorkspacePackageResource): boolean {
    if (resource.status !== "implied") {
      return false
    }
    const users = resource.implied_by ?? []
    return users.length === 0 || users.some((name) => rootNames.has(name))
  }

  function isInPackage(resource: WorkspacePackageResource): boolean {
    return (
      selected.has(getPackageResourceKey(resource)) ||
      isImplied(resource) ||
      isSelectedByOtherPattern(resource, patterns)
    )
  }

  /**
   * Why a row cannot be toggled, or undefined when it can. Implied resources
   * follow the resources that use them; glob-selected ones follow their glob.
   */
  function lockedReason(
    resource: WorkspacePackageResource
  ): string | undefined {
    if (isImplied(resource)) {
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

  return { isInPackage, lockedReason }
}

interface PackageSummaryProps {
  workspacePackage: WorkspacePackageRead
  selected: Set<string>
  isChanged: boolean
  disabled?: boolean
  onApply: (selected: Set<string>) => void
}

/**
 * One-row package summary for the push page. It says how much of the workspace
 * is in the package and opens the resource dialog to change that.
 */
export function PackageSummary({
  workspacePackage,
  selected,
  isChanged,
  disabled = false,
  onApply,
}: PackageSummaryProps) {
  const { user } = useAuth()
  const resources = workspacePackage.resources ?? []
  const { isInPackage } = getPackageMembership(workspacePackage, selected)
  const ref = workspacePackage.ref ?? "the default branch"

  return (
    <section
      aria-label="Package"
      className="flex items-center gap-3 rounded-md border px-4 py-3"
    >
      <PackageIcon className="size-4 shrink-0 text-muted-foreground" />
      <div className="min-w-0 flex-1 space-y-0.5">
        <p className="text-sm font-medium">
          {resources.filter(isInPackage).length} of {resources.length} resources
          in the package
        </p>
        <p className="text-xs text-muted-foreground">
          {isChanged
            ? `Edited from ${PACKAGE_INCLUDE_FILENAME} on ${ref}. `
            : `From ${PACKAGE_INCLUDE_FILENAME} on ${ref}. `}
          Publishing changes no customer workspace. Deploy a release from{" "}
          {user?.isSuperuser ? (
            <Link
              href={MANAGED_PACKAGE_ADMIN_PATH}
              className="font-medium text-foreground underline underline-offset-2"
            >
              {MANAGED_PACKAGE_ADMIN_LABEL}
            </Link>
          ) : (
            MANAGED_PACKAGE_ADMIN_LABEL
          )}
          .
        </p>
      </div>
      <PackageResourcesDialog
        trigger={
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="shrink-0"
            disabled={disabled}
          >
            Edit resources
          </Button>
        }
        workspacePackage={workspacePackage}
        selected={selected}
        onApply={onApply}
      />
    </section>
  )
}

/**
 * Dialog for choosing what the package contains. Edits stay in a draft until
 * applied, so closing it discards them and the push preview is left alone.
 */
export function PackageResourcesDialog({
  trigger,
  workspacePackage,
  selected,
  onApply,
}: {
  trigger: ReactNode
  workspacePackage: WorkspacePackageRead
  selected: Set<string>
  onApply: (selected: Set<string>) => void
}) {
  const [open, setOpen] = useState(false)

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent size="xl" className="max-w-3xl">
        <DialogHeader>
          <DialogTitle>Choose package resources</DialogTitle>
          <DialogDescription>
            Checked resources and everything they use are published. Other
            resource files are removed from the branch.
          </DialogDescription>
        </DialogHeader>
        <PackageResourcesForm
          workspacePackage={workspacePackage}
          selected={selected}
          onApply={(next) => {
            onApply(next)
            setOpen(false)
          }}
        />
      </DialogContent>
    </Dialog>
  )
}

/** Dialog content. It mounts on open, so each open starts from the selection. */
function PackageResourcesForm({
  workspacePackage,
  selected,
  onApply,
}: {
  workspacePackage: WorkspacePackageRead
  selected: Set<string>
  onApply: (selected: Set<string>) => void
}) {
  // A draft only applies to the package it was edited against, so a refetch
  // that changes the package restarts it from the current selection.
  const [edit, setEdit] = useState<{
    source: WorkspacePackageRead
    draft: Set<string>
  }>()
  const draft = edit && edit.source === workspacePackage ? edit.draft : selected
  const [search, setSearch] = useState("")
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set())
  const resources = workspacePackage.resources ?? []
  const { isInPackage, lockedReason } = getPackageMembership(
    workspacePackage,
    draft
  )
  const query = search.trim().toLowerCase()
  const groups = groupPackageResources(
    resources.filter((resource) => resource.name.toLowerCase().includes(query))
  )

  function setChecked(items: WorkspacePackageResource[], checked: boolean) {
    const next = new Set(draft)
    for (const resource of items) {
      const key = getPackageResourceKey(resource)
      if (checked) {
        next.add(key)
      } else {
        next.delete(key)
      }
    }
    setEdit({ source: workspacePackage, draft: next })
  }

  function toggleExpanded(type: string) {
    const next = new Set(expanded)
    if (!next.delete(type)) {
      next.add(type)
    }
    setExpanded(next)
  }

  const emptyMessage =
    resources.length === 0
      ? "This workspace has no resources to package."
      : "No resources match your search."

  return (
    <>
      <DialogBody>
        <div className="relative">
          <SearchIcon className="absolute left-2.5 top-2.5 size-3.5 text-muted-foreground" />
          <Input
            aria-label="Search resources"
            placeholder="Search resources"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            className="h-8 pl-8"
          />
        </div>
        {groups.length === 0 ? (
          <p className="py-6 text-center text-xs text-muted-foreground">
            {emptyMessage}
          </p>
        ) : (
          <ul className="space-y-3">
            {groups.map((group) => (
              <PackageResourceGroup
                key={group.type}
                group={group}
                // Searching looks across every group.
                isOpen={query !== "" || expanded.has(group.type)}
                canToggleOpen={query === ""}
                isInPackage={isInPackage}
                lockedReason={lockedReason}
                onToggleOpen={() => toggleExpanded(group.type)}
                onChecked={setChecked}
              />
            ))}
          </ul>
        )}
      </DialogBody>
      <DialogFooter className="sm:justify-between">
        <span className="text-xs text-muted-foreground">
          {resources.filter(isInPackage).length} of {resources.length} in the
          package. Dependencies reflect{" "}
          {workspacePackage.ref ?? "the default branch"}. Preview to see the
          effect of your edits.
        </span>
        <Button type="button" size="sm" onClick={() => onApply(draft)}>
          Apply selection
        </Button>
      </DialogFooter>
    </>
  )
}

function PackageResourceGroup({
  group,
  isOpen,
  canToggleOpen,
  isInPackage,
  lockedReason,
  onToggleOpen,
  onChecked,
}: {
  group: PackageGroup
  isOpen: boolean
  canToggleOpen: boolean
  isInPackage: (resource: WorkspacePackageResource) => boolean
  lockedReason: (resource: WorkspacePackageResource) => string | undefined
  onToggleOpen: () => void
  onChecked: (items: WorkspacePackageResource[], checked: boolean) => void
}) {
  const label = getWorkspaceSyncResourceLabel(group.type)
  // Implied resources follow their users, so checking a group never adds them.
  // That would give each one its own include line.
  const addable = group.resources.filter(
    (resource) => resource.status !== "implied" && !isInPackage(resource)
  )
  const hasRemovable = group.resources.some(
    (resource) => isInPackage(resource) && lockedReason(resource) === undefined
  )
  const count = group.resources.filter(isInPackage).length
  let groupChecked: boolean | "indeterminate" = "indeterminate"
  if (count === 0) {
    groupChecked = false
  } else if (count === group.resources.length) {
    groupChecked = true
  }

  return (
    <li>
      <div className="flex h-8 items-center gap-2.5">
        <Checkbox
          aria-label={`Select all ${label}`}
          checked={groupChecked}
          disabled={addable.length === 0 && !hasRemovable}
          onCheckedChange={() => {
            if (addable.length > 0) {
              onChecked(addable, true)
            } else {
              onChecked(group.resources, false)
            }
          }}
        />
        <button
          type="button"
          aria-expanded={isOpen}
          disabled={!canToggleOpen}
          onClick={onToggleOpen}
          className="flex min-w-0 flex-1 items-center gap-1.5 text-left"
        >
          <span className="truncate text-sm font-medium">{label}</span>
          <ChevronRightIcon
            className={cn(
              "size-3.5 shrink-0 text-muted-foreground transition-transform",
              isOpen && "rotate-90"
            )}
          />
          <span className="ml-auto shrink-0 text-xs text-muted-foreground">
            {count} of {group.resources.length}
          </span>
        </button>
      </div>
      {isOpen && (
        <ul className="max-h-60 overflow-y-auto pr-2">
          {group.resources.map((resource) => (
            <PackageResourceRow
              key={getPackageResourceKey(resource)}
              resource={resource}
              checked={isInPackage(resource)}
              lockedReason={lockedReason(resource)}
              onChecked={onChecked}
            />
          ))}
        </ul>
      )}
    </li>
  )
}

function PackageResourceRow({
  resource,
  checked,
  lockedReason,
  onChecked,
}: {
  resource: WorkspacePackageResource
  checked: boolean
  lockedReason: string | undefined
  onChecked: (items: WorkspacePackageResource[], checked: boolean) => void
}) {
  const id = `package-resource-${resource.resource_type}-${resource.source_id}`
  return (
    <li className="flex items-start gap-2.5 py-1 pl-6">
      <Checkbox
        id={id}
        className="mt-0.5"
        checked={checked}
        disabled={lockedReason !== undefined}
        onCheckedChange={(value) => onChecked([resource], value === true)}
      />
      <label
        htmlFor={id}
        title={resource.path}
        className="min-w-0 flex-1 cursor-pointer"
      >
        <span className="flex items-baseline gap-3">
          <span className="min-w-0 truncate text-sm">{resource.name}</span>
          <span className="ml-auto min-w-0 truncate font-mono text-[11px] text-muted-foreground">
            {resource.path}
          </span>
        </span>
        {lockedReason && (
          <span className="block text-xs text-muted-foreground">
            {lockedReason}
          </span>
        )}
      </label>
    </li>
  )
}
