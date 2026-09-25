"use client"

import { ChevronRightIcon, CircleMinusIcon, CirclePlusIcon } from "lucide-react"
import { type ReactNode, useState } from "react"
import type {
  ScimActivationReviewRead,
  ScimDirectoryUserRead,
  ScimGroupTransitionRead,
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

/** The IdP groups a change adds or removes as sources of a Tracecat group. */
function SourceChanges({
  added,
  removed,
}: {
  added: string[]
  removed: string[]
}) {
  if (added.length === 0 && removed.length === 0) return null
  return (
    <div className="flex flex-wrap gap-x-3 gap-y-1 pb-2 text-xs text-muted-foreground">
      {added.map((name) => (
        <span key={`added-${name}`} className="flex items-center gap-1">
          <CirclePlusIcon
            aria-label="Source added"
            className="size-3 text-diff-marker-added"
          />
          {name}
        </span>
      ))}
      {removed.map((name) => (
        <span key={`removed-${name}`} className="flex items-center gap-1">
          <CircleMinusIcon
            aria-label="Source removed"
            className="size-3 text-diff-marker-removed"
          />
          {name}
        </span>
      ))}
    </div>
  )
}

/** One collapsible change: a title, its counts, and the diff behind it. */
function ChangeRow({
  title,
  counts,
  lines,
  defaultOpen,
  children,
}: {
  title: ReactNode
  counts: CountPart[]
  lines: DiffLine[]
  defaultOpen: boolean
  children?: ReactNode
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
        <span className="min-w-0 flex-1 truncate text-sm font-medium">
          {title}
        </span>
        <Counts parts={counts} />
      </CollapsibleTrigger>
      <CollapsibleContent>
        <div className="px-3 pb-3 pl-8">
          {children}
          <MembershipDiff lines={lines} />
        </div>
      </CollapsibleContent>
    </Collapsible>
  )
}

const MAX_LISTED = 10

const SOURCE_LABEL = { manual: "manual", idp: "IdP" } as const

function groupLines(group: ScimGroupTransitionRead): DiffLine[] {
  const email = (value: string) => value || "User details unavailable"
  const from = (source: "manual" | "idp" | null | undefined) =>
    source ? SOURCE_LABEL[source] : "manual"
  const lines: DiffLine[] = []
  for (const change of group.changes) {
    if (change.kind === "lose") {
      lines.push({
        key: change.user_id,
        kind: "removed",
        text: email(change.email),
        note: `${from(change.from_source)} → removed`,
      })
    }
  }
  const gains = group.changes.filter((change) => change.kind === "gain")
  for (const change of gains.slice(0, MAX_LISTED)) {
    lines.push({
      key: change.user_id,
      kind: "added",
      text: email(change.email),
    })
  }
  if (gains.length > MAX_LISTED) {
    lines.push({
      key: "gain-more",
      kind: "added",
      text: plural(gains.length - MAX_LISTED, "more user", "more users"),
    })
  }
  const moves = group.changes.filter(
    (change) => change.kind === "to_idp" || change.kind === "to_manual"
  )
  for (const change of moves.slice(0, MAX_LISTED)) {
    lines.push({
      key: change.user_id,
      kind: "modified",
      text: email(change.email),
      note: change.kind === "to_idp" ? "manual → IdP" : "IdP → manual",
    })
  }
  if (moves.length > MAX_LISTED) {
    lines.push({
      key: "move-more",
      kind: "modified",
      text: plural(moves.length - MAX_LISTED, "more user", "more users"),
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

function groupCounts(group: ScimGroupTransitionRead): CountPart[] {
  const count = (kinds: string[]) =>
    group.changes.filter((change) => kinds.includes(change.kind)).length
  return [
    { kind: "added", value: count(["gain"]) },
    { kind: "removed", value: count(["lose"]) },
    { kind: "modified", value: count(["to_idp", "to_manual"]) },
  ]
}

function userLine(user: ScimDirectoryUserRead): DiffLine {
  if (user.active) {
    return { key: user.id, kind: "added", text: user.email, note: "joins" }
  }
  if (user.is_member) {
    // Activation deprovisions inactive members, with their roles and groups.
    return {
      key: user.id,
      kind: "removed",
      text: user.email,
      note: "inactive → leaves the organization",
    }
  }
  return {
    key: user.id,
    kind: "unchanged",
    text: user.email,
    note: "inactive, skipped",
  }
}

function usersLines(
  pushedCount: number,
  listedUsers: ScimDirectoryUserRead[]
): DiffLine[] {
  if (pushedCount === 0) {
    return [
      { key: "none", kind: "unchanged", text: "No users have been pushed yet" },
    ]
  }
  if (listedUsers.length === 0) {
    return [{ key: "none", kind: "unchanged", text: "No membership changes" }]
  }
  return listedUsers.map(userLine)
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
  const joining = review.users.filter((user) => user.active && !user.is_member)
  // Activation leaves active users who are already members unchanged.
  const listedUsers = review.users.filter(
    (user) => !(user.active && user.is_member)
  )
  const leaving = review.users.filter((user) => !user.active && user.is_member)
  const groups = review.groups ?? []
  const missingLabels = groups.some((group) =>
    group.changes.some((change) => !change.email)
  )
  const changeCount = groups.reduce(
    (total, group) =>
      total + group.added_sources.length + group.removed_sources.length,
    0
  )
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
              title="Organization members"
              counts={[
                { kind: "added", value: joining.length },
                { kind: "removed", value: leaving.length },
              ]}
              defaultOpen={leaving.length > 0}
              lines={usersLines(review.users.length, listedUsers)}
            />
          )}
          {groups.map((group) => (
            <ChangeRow
              key={group.group_id}
              title={group.group_name}
              counts={groupCounts(group)}
              defaultOpen={group.changes.some(
                (change) => change.kind === "lose"
              )}
              lines={groupLines(group)}
            >
              <SourceChanges
                added={group.added_sources}
                removed={group.removed_sources}
              />
            </ChangeRow>
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
