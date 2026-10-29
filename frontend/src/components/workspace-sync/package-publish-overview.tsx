"use client"

import { ArrowRightIcon, PackageIcon } from "lucide-react"
import Link from "next/link"
import type { PullResourceDiff } from "@/client"
import { getWorkspaceSyncResourceLabel } from "@/components/workspace-sync/resource-metadata"
import { cn } from "@/lib/utils"

/** Path of the platform admin page where releases are cut and deployed. */
export const MANAGED_PACKAGE_ADMIN_PATH = "/admin/managed-package"

const PUBLISH_STEPS = [
  { title: "Publish package", where: "This page" },
  { title: "Cut a release", where: "Admin" },
  { title: "Deploy to customers", where: "Admin" },
] as const

/**
 * Explains that pushing from the managed resources source publishes the
 * package to Git and changes no customer workspace, then names the two
 * later steps that do.
 */
export function PackagePublishSteps({ branch }: { branch: string }) {
  return (
    <div className="space-y-2.5 rounded-md border px-4 py-3">
      <div className="flex items-center gap-2 text-sm font-medium">
        <PackageIcon className="size-4 text-muted-foreground" />
        This workspace publishes the managed resources package
      </div>
      <ol className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
        {PUBLISH_STEPS.map((step, index) => (
          <li key={step.title} className="flex items-center gap-2">
            {index > 0 && (
              <ArrowRightIcon className="size-3 text-muted-foreground" />
            )}
            <span
              className={cn(
                "rounded border px-1.5 py-0.5",
                index === 0
                  ? "border-foreground/40 font-medium text-foreground"
                  : "text-muted-foreground"
              )}
            >
              {index + 1}. {step.title}
              <span className="ml-1 text-muted-foreground">({step.where})</span>
            </span>
          </li>
        ))}
      </ol>
      <p className="text-xs text-muted-foreground">
        Publishing writes the selected resources and everything they use to{" "}
        <span className="font-medium text-foreground">
          {branch || "the branch you pick"}
        </span>
        , and removes anything you unselected. No customer workspace changes.
        Customers only receive a release tag that is deployed from{" "}
        <Link
          href={MANAGED_PACKAGE_ADMIN_PATH}
          className="font-medium text-foreground underline underline-offset-2"
        >
          Admin, Managed package
        </Link>
        .
      </p>
    </div>
  )
}

const CHANGE_GROUPS = [
  { change: "added", label: "Added to the package" },
  { change: "modified", label: "Updated in the package" },
  { change: "deleted", label: "Removed from the package" },
] as const

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
  const groups = CHANGE_GROUPS.map((group) => ({
    ...group,
    items: diffs.filter((diff) => diff.change_type === group.change),
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
              {group.items.map((diff) => (
                <li
                  key={`${diff.resource_type}:${diff.source_id}`}
                  className="rounded border px-1.5 py-0.5"
                >
                  <span className="text-muted-foreground">
                    {getWorkspaceSyncResourceLabel(diff.resource_type)}:
                  </span>{" "}
                  {diff.title ?? diff.source_id}
                </li>
              ))}
            </ul>
          </div>
        ))
      )}
    </div>
  )
}
