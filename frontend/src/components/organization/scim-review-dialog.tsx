"use client"

import { ChevronRightIcon, CircleMinusIcon, CirclePlusIcon } from "lucide-react"
import { type ReactNode, useState } from "react"
import type {
  ScimActivationReviewRead,
  ScimMappingPlanRead,
  ScimRemovalPlanRead,
} from "@/client"
import { Button } from "@/components/ui/button"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { cn } from "@/lib/utils"

type DiffKind = "added" | "removed" | "modified" | "unchanged"

type DiffLine = { key: string; kind: DiffKind; text: string; note?: string }

const DIFF_MARKER: Record<DiffKind, string> = {
  added: "+",
  removed: "-",
  modified: "~",
  unchanged: "",
}

function plural(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`
}

/** Membership changes in the house unified-diff style. */
function MembershipDiff({ lines }: { lines: DiffLine[] }) {
  return (
    <div className="overflow-hidden rounded-md border bg-background py-1 font-mono text-xs leading-5">
      {lines.map((line) => (
        <div
          key={line.key}
          className={cn(
            "grid grid-cols-[1.25rem_minmax(0,1fr)_auto] gap-x-2 pr-2",
            line.kind === "added" && "bg-diff-added text-diff-added-foreground",
            line.kind === "removed" &&
              "bg-diff-removed text-diff-removed-foreground",
            line.kind === "modified" &&
              "bg-amber-50 text-amber-900 dark:bg-amber-500/15 dark:text-amber-200"
          )}
        >
          <span
            className={cn(
              "select-none text-center",
              line.kind === "added" && "text-diff-marker-added",
              line.kind === "removed" && "text-diff-marker-removed",
              line.kind === "modified" && "text-amber-600 dark:text-amber-400"
            )}
          >
            {DIFF_MARKER[line.kind]}
          </span>
          <span className="truncate">{line.text}</span>
          {line.note ? (
            <span className="font-sans text-muted-foreground">{line.note}</span>
          ) : null}
        </div>
      ))}
    </div>
  )
}

/** Mapping-level change, shown with the workspace-sync change icons. */
type RowChange = "added" | "removed"

type CountPart = { kind: Exclude<DiffKind, "unchanged">; value: number }

const COUNT_SIGN: Record<CountPart["kind"], string> = {
  added: "+",
  removed: "−",
  modified: "~",
}

const COUNT_CLASS: Record<CountPart["kind"], string> = {
  added: "text-diff-marker-added",
  removed: "text-diff-marker-removed",
  modified: "text-amber-600 dark:text-amber-400",
}

/** People counts per change kind, colored like their diff lines. */
function Counts({ parts }: { parts: CountPart[] }) {
  return (
    <span className="flex shrink-0 gap-2 font-mono text-[11px]">
      {parts
        .filter((part) => part.value > 0)
        .map((part) => (
          <span key={part.kind} className={COUNT_CLASS[part.kind]}>
            {COUNT_SIGN[part.kind]}
            {part.value}
          </span>
        ))}
    </span>
  )
}

/** One collapsible change: a title, its counts, and the diff behind it. */
function ChangeRow({
  change,
  title,
  counts,
  lines,
  defaultOpen,
}: {
  change: RowChange
  title: ReactNode
  counts: CountPart[]
  lines: DiffLine[]
  defaultOpen: boolean
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <CollapsibleTrigger className="flex w-full items-center gap-2 px-3 py-2.5 text-left outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring">
        <ChevronRightIcon
          className={cn(
            "size-3.5 shrink-0 text-muted-foreground transition-transform",
            open && "rotate-90"
          )}
        />
        {change === "added" ? (
          <CirclePlusIcon
            aria-label="Added"
            className="size-3.5 shrink-0 text-diff-marker-added"
          />
        ) : (
          <CircleMinusIcon
            aria-label="Removed"
            className="size-3.5 shrink-0 text-diff-marker-removed"
          />
        )}
        <span className="min-w-0 flex-1 truncate text-sm font-medium">
          {title}
        </span>
        <Counts parts={counts} />
      </CollapsibleTrigger>
      <CollapsibleContent>
        <div className="px-3 pb-3 pl-[3.25rem]">
          <MembershipDiff lines={lines} />
        </div>
      </CollapsibleContent>
    </Collapsible>
  )
}

const MAX_LISTED = 10

function planLines(plan: ScimMappingPlanRead): DiffLine[] {
  const losing = new Set(plan.users_losing_access)
  const inSource = new Set(plan.manual_members_in_source ?? [])
  const manualEmail = (id: string) =>
    plan.manual_member_emails?.[id] ?? "User details unavailable"
  const lines: DiffLine[] = plan.users_losing_access.map((id) => ({
    key: `lose-${id}`,
    kind: "removed",
    text: manualEmail(id),
    note: "manual → removed",
  }))
  const gaining = plan.users_gaining_access
  for (const id of gaining.slice(0, MAX_LISTED)) {
    lines.push({
      key: `gain-${id}`,
      kind: "added",
      text: plan.gaining_member_emails?.[id] ?? "User details unavailable",
    })
  }
  if (gaining.length > MAX_LISTED) {
    lines.push({
      key: "gain-more",
      kind: "added",
      text: `${plural(gaining.length - MAX_LISTED, "more user", "more users")}`,
    })
  }
  for (const id of plan.manual_members_purged) {
    if (losing.has(id)) continue
    lines.push({
      key: `keep-${id}`,
      kind: "modified",
      text: manualEmail(id),
      note: inSource.has(id) ? "manual → IdP" : "manual → IdP (other mapping)",
    })
  }
  if (lines.length === 0) {
    lines.push({
      key: "none",
      kind: "unchanged",
      text: "No membership changes",
    })
  }
  return lines
}

function removalLines(plan: ScimRemovalPlanRead): DiffLine[] {
  const email = (id: string) =>
    plan.member_emails?.[id] ?? "User details unavailable"
  const lines: DiffLine[] = [
    ...plan.losing_access.map((id) => ({
      key: `lose-${id}`,
      kind: "removed" as const,
      text: email(id),
      note: "IdP → removed",
    })),
    ...plan.becoming_manual.map((id) => ({
      key: `manual-${id}`,
      kind: "modified" as const,
      text: email(id),
      note: "IdP → manual",
    })),
  ]
  if (lines.length === 0) {
    lines.push({
      key: "none",
      kind: "unchanged",
      text: "No membership changes",
    })
  }
  return lines
}

function removalCounts(plan: ScimRemovalPlanRead): CountPart[] {
  return [
    { kind: "removed", value: plan.losing_access.length },
    { kind: "modified", value: plan.becoming_manual.length },
  ]
}

function planCounts(plan: ScimMappingPlanRead): CountPart[] {
  const losing = new Set(plan.users_losing_access)
  return [
    { kind: "added", value: plan.users_gaining_access.length },
    { kind: "removed", value: losing.size },
    {
      kind: "modified",
      value: plan.manual_members_purged.filter((id) => !losing.has(id)).length,
    },
  ]
}

/** Review what activation or a batch of mapping changes does, then confirm. */
export function ScimReviewDialog({
  review,
  activation,
  pending,
  onClose,
  onConfirm,
}: {
  review: ScimActivationReviewRead
  activation: boolean
  pending: boolean
  onClose: () => void
  onConfirm: () => Promise<void>
}) {
  const joining = review.users.filter((user) => user.active)
  const missingLabels = review.plans.some((plan) =>
    plan.manual_members_purged.some((id) => !plan.manual_member_emails?.[id])
  )
  const removals = review.removals ?? []
  const changeCount = review.plans.length + removals.length
  const confirmLabel = activation
    ? `Activate for ${plural(joining.length, "user", "users")}`
    : `Apply ${plural(changeCount, "change", "changes")}`

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !pending) onClose()
      }}
    >
      <DialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>
            {activation
              ? "Activate SCIM provisioning"
              : "Review mapping changes"}
          </DialogTitle>
          <DialogDescription>
            Access from other roles and groups is unchanged.
          </DialogDescription>
        </DialogHeader>

        <div className="divide-y rounded-md border">
          {activation && (
            <ChangeRow
              change="added"
              title="Organization members"
              counts={[{ kind: "added", value: joining.length }]}
              defaultOpen={false}
              lines={
                review.users.length === 0
                  ? [
                      {
                        key: "none",
                        kind: "unchanged",
                        text: "No users have been pushed yet",
                      },
                    ]
                  : review.users.map((user) => ({
                      key: user.id,
                      kind: user.active ? "added" : "unchanged",
                      text: user.email,
                      note: user.active ? "joins" : "inactive, skipped",
                    }))
              }
            />
          )}
          {review.plans.map((plan) => (
            <ChangeRow
              change="added"
              key={`${plan.external_group_id}-${plan.group_id}`}
              title={`${plan.external_group_display_name} → ${plan.group_name}`}
              counts={planCounts(plan)}
              defaultOpen={plan.users_losing_access.length > 0}
              lines={planLines(plan)}
            />
          ))}
          {removals.map((plan) => (
            <ChangeRow
              key={plan.mapping_id}
              title={`${plan.external_group_display_name} → ${plan.group_name}`}
              change="removed"
              counts={removalCounts(plan)}
              defaultOpen
              lines={removalLines(plan)}
            />
          ))}
        </div>

        {missingLabels && (
          <p role="alert" className="text-sm text-destructive">
            Affected user details could not be loaded. Close this review and try
            again before confirming.
          </p>
        )}

        <DialogFooter>
          <Button variant="outline" disabled={pending} onClick={onClose}>
            Cancel
          </Button>
          <Button
            disabled={pending || missingLabels}
            onClick={() => {
              void onConfirm().catch(() => {})
            }}
          >
            {pending ? "Applying…" : confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
