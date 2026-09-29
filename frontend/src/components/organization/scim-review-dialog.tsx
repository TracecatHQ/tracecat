"use client"

import { ChevronRightIcon, Loader2 } from "lucide-react"
import { type ReactNode, useId, useState } from "react"
import type {
  ScimActivationReviewRead,
  ScimGroupTransitionRead,
  ScimReviewPeople,
} from "@/client"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { formatRelative } from "@/lib/time"
import { cn } from "@/lib/utils"

type Section = "leave" | "lose" | "join" | "to_idp" | "to_manual"

type PersonLine = { key: string; email: string; note?: string }

function plural(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`
}

function Avatar({ email, stacked }: { email: string; stacked?: boolean }) {
  return (
    <span
      aria-hidden
      className={cn(
        "flex size-6 shrink-0 items-center justify-center rounded-full bg-muted text-[11px] font-medium text-muted-foreground",
        stacked && "-ml-2 border-2 border-background first:ml-0"
      )}
    >
      {(email[0] ?? "?").toUpperCase()}
    </span>
  )
}

/** People rows; scrolls once expanded past the preview. */
function PeopleList({
  lines,
  expanded,
  loading,
}: {
  lines: PersonLine[]
  expanded: boolean
  loading: boolean
}) {
  return (
    <div
      className={cn(
        "divide-y rounded-md border",
        expanded && "max-h-72 overflow-y-auto"
      )}
    >
      {lines.map((line) => (
        <div key={line.key} className="flex items-center gap-2.5 px-3 py-2">
          <Avatar email={line.email} />
          <span className="min-w-0 flex-1 truncate text-sm">
            {line.email || "User details unavailable"}
          </span>
          {line.note ? (
            <span className="shrink-0 text-xs text-muted-foreground">
              {line.note}
            </span>
          ) : null}
        </div>
      ))}
      {loading ? (
        <div className="flex items-center gap-2 px-3 py-2 text-xs text-muted-foreground">
          <Loader2 className="size-3 animate-spin" />
          Loading everyone…
        </div>
      ) : null}
    </div>
  )
}

/** A collapsed summary line that opens its people list. */
function CollapsedRow({
  open,
  onToggle,
  leading,
  label,
  hint,
  trailing,
  children,
}: {
  open: boolean
  onToggle: () => void
  leading?: ReactNode
  label: string
  /** Why this happens, shown on hovering the label. */
  hint?: string
  trailing: string
  children: ReactNode
}) {
  const hintId = useId()
  return (
    <div className="space-y-2">
      <button
        type="button"
        aria-expanded={open}
        aria-describedby={hint ? hintId : undefined}
        onClick={onToggle}
        className="flex w-full items-center gap-2.5 rounded-md border px-3 py-2 text-left text-sm outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring"
      >
        <ChevronRightIcon
          className={cn(
            "size-3.5 shrink-0 text-muted-foreground transition-transform",
            open && "rotate-90"
          )}
        />
        {leading}
        <span className="min-w-0 flex-1 truncate">
          {hint ? (
            <Tooltip>
              <TooltipTrigger asChild>
                <span className="cursor-help underline decoration-dotted underline-offset-2">
                  {label}
                </span>
              </TooltipTrigger>
              <TooltipContent className="max-w-xs">{hint}</TooltipContent>
            </Tooltip>
          ) : (
            label
          )}
        </span>
        <span className="shrink-0 text-xs text-muted-foreground">
          {trailing}
        </span>
        {hint ? (
          <span id={hintId} className="sr-only">
            {hint}
          </span>
        ) : null}
      </button>
      {open ? children : null}
    </div>
  )
}

function groupNote(group: ScimGroupTransitionRead, activation: boolean) {
  if (activation) {
    return group.added_sources.length > 0
      ? `from ${group.added_sources.join(", ")}`
      : null
  }
  const parts = [
    group.added_sources.length > 0
      ? `Added ${group.added_sources.join(", ")}`
      : null,
    group.removed_sources.length > 0
      ? `removed ${group.removed_sources.join(", ")}`
      : null,
  ].filter(Boolean)
  return parts.length > 0 ? parts.join(" · ") : null
}

/** Explain that a first mapping replaces the group's manual membership. */
function TakeoverNote({ group }: { group: ScimGroupTransitionRead }) {
  const sources = group.added_sources.join(", ")
  return (
    <p className="mt-0.5 text-xs text-muted-foreground">
      Becomes IdP-managed.
      {group.lost > 0 ? (
        <>
          {" "}
          <Tooltip>
            <TooltipTrigger asChild>
              <span
                tabIndex={0}
                className="cursor-help underline decoration-dotted underline-offset-2 outline-none focus-visible:ring-1 focus-visible:ring-ring"
              >
                Removes{" "}
                {plural(
                  group.lost,
                  "previously added member",
                  "previously added members"
                )}
                .
              </span>
            </TooltipTrigger>
            <TooltipContent className="max-w-xs">
              Membership now comes from {sources}. People added to this group
              before who are not in {sources} are removed.
            </TooltipContent>
          </Tooltip>
        </>
      ) : null}
    </p>
  )
}

function groupCounts(group: ScimGroupTransitionRead): string {
  const parts = [
    group.gained > 0 ? `${group.gained} added` : null,
    group.lost > 0 ? `${group.lost} removed` : null,
  ].filter(Boolean)
  return parts.length > 0 ? parts.join(" · ") : "No membership change"
}

function reviewSummary(
  activation: boolean,
  joinCount: number,
  loseCount: number
): string {
  if (activation) {
    const joins = `${plural(joinCount, "person joins", "people join")} the organization`
    return loseCount > 0
      ? `${joins} and ${plural(loseCount, "person loses", "people lose")} access.`
      : `${joins}.`
  }
  if (loseCount > 0) {
    return `${plural(loseCount, "person loses", "people lose")} group access.`
  }
  return "No one loses access."
}

function isTruncated(people: ScimReviewPeople): boolean {
  return people.items.length < people.count
}

/**
 * Review who activation or a batch of mapping changes affects, then confirm.
 *
 * Lists arrive as previews with true counts. "Show all" asks the parent for
 * the full review once; every list then reads from it.
 */
export function ScimReviewDialog({
  review,
  activation,
  complete,
  loadingAll,
  lastRequestAt,
  pending,
  onShowAll,
  onClose,
  onConfirm,
}: {
  review: ScimActivationReviewRead
  activation: boolean
  complete: boolean
  loadingAll: boolean
  lastRequestAt?: string | null
  pending: boolean
  onShowAll: () => void
  onClose: () => void
  onConfirm: () => Promise<void>
}) {
  const [open, setOpen] = useState<Set<Section>>(new Set())

  function toggle(section: Section, people: ScimReviewPeople[]) {
    const next = new Set(open)
    if (next.has(section)) {
      next.delete(section)
    } else {
      next.add(section)
      if (!complete && people.some(isTruncated)) onShowAll()
    }
    setOpen(next)
  }

  const leaving = activation
    ? review.leaving
    : { count: 0, items: [] as ScimReviewPeople["items"] }
  const joining = activation
    ? review.joining
    : { count: 0, items: [] as ScimReviewPeople["items"] }
  const loseCount = leaving.count + review.losing.count
  const losses = [
    {
      section: "leave" as const,
      people: leaving,
      label: `${plural(leaving.count, "person leaves", "people leave")} the organization`,
      hint: "Your IdP marks them inactive, so turning on SCIM removes them from this organization.",
      lines: leaving.items.map((person) => ({
        key: `leave-${person.user_id}`,
        email: person.email,
      })),
    },
    {
      section: "lose" as const,
      people: review.losing,
      label: `${plural(review.losing.count, "person loses", "people lose")} group access`,
      hint: "Your IdP doesn't list them in the groups mapped to these Tracecat groups, so they're removed from them.",
      lines: review.losing.items.map((person) => ({
        key: `lose-${person.user_id}`,
        email: person.email,
        note: `Removed from ${(person.groups ?? []).join(", ")}`,
      })),
    },
  ].filter((loss) => loss.people.count > 0)
  const loseLines: PersonLine[] = losses.flatMap((loss) => loss.lines)
  const moves = [
    {
      section: "to_idp" as const,
      people: review.to_idp,
      label: `${plural(review.to_idp.count, "manual member becomes", "manual members become")} IdP-managed`,
    },
    {
      section: "to_manual" as const,
      people: review.to_manual,
      label: `${plural(review.to_manual.count, "IdP member becomes", "IdP members become")} manual`,
    },
  ].filter((move) => move.people.count > 0)

  const missingLabels = [
    ...loseLines,
    ...moves.flatMap((move) => move.people.items),
    ...joining.items,
  ].some((line) => !line.email)
  const changeCount = review.groups.reduce(
    (total, group) =>
      total + group.added_sources.length + group.removed_sources.length,
    0
  )
  const lastRequest = formatRelative(lastRequestAt)
  const summary = reviewSummary(activation, joining.count, loseCount)
  const confirmLabel = activation
    ? `Activate for ${plural(joining.count, "user", "users")}`
    : `Apply ${plural(changeCount, "change", "changes")}`
  const nothingChanges =
    loseCount === 0 &&
    joining.count === 0 &&
    moves.length === 0 &&
    review.groups.length === 0

  return (
    <Dialog
      open
      onOpenChange={(next) => {
        if (!next && !pending) onClose()
      }}
    >
      <DialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>
            {activation
              ? "Activate SCIM provisioning"
              : "Review mapping changes"}
          </DialogTitle>
          <DialogDescription className="text-foreground/80">
            {summary}
          </DialogDescription>
          <p className="text-xs text-muted-foreground">
            {lastRequest
              ? `Last request from your identity provider ${lastRequest}. `
              : null}
            Access from other roles and groups is unchanged.
          </p>
        </DialogHeader>

        <div className="space-y-6">
          {loseCount > 0 && (
            <section className="space-y-2">
              <h3 className="text-xs font-semibold text-rose-700 dark:text-rose-400">
                Lose access · {loseCount}
              </h3>
              {losses.map((loss) => (
                <CollapsedRow
                  key={loss.section}
                  open={open.has(loss.section)}
                  onToggle={() => toggle(loss.section, [loss.people])}
                  leading={
                    <span className="flex shrink-0">
                      {loss.lines.slice(0, 4).map((line) => (
                        <Avatar key={line.key} email={line.email} stacked />
                      ))}
                    </span>
                  }
                  label={loss.label}
                  hint={loss.hint}
                  trailing={open.has(loss.section) ? "Hide" : "Show all"}
                >
                  <PeopleList
                    lines={loss.lines}
                    expanded
                    loading={loadingAll}
                  />
                </CollapsedRow>
              ))}
            </section>
          )}

          {joining.count > 0 && (
            <section className="space-y-2">
              <h3 className="text-xs font-semibold">
                Join the organization · {joining.count}
              </h3>
              <CollapsedRow
                open={open.has("join")}
                onToggle={() => toggle("join", [joining])}
                leading={
                  <span className="flex shrink-0">
                    {joining.items.slice(0, 4).map((person) => (
                      <Avatar
                        key={person.user_id}
                        email={person.email}
                        stacked
                      />
                    ))}
                  </span>
                }
                label={`${plural(joining.count, "person", "people")} from your directory`}
                trailing={open.has("join") ? "Hide" : "Show all"}
              >
                <PeopleList
                  lines={joining.items.map((person) => ({
                    key: person.user_id,
                    email: person.email,
                  }))}
                  expanded
                  loading={loadingAll}
                />
              </CollapsedRow>
            </section>
          )}

          {review.groups.length > 0 && (
            <section className="space-y-2">
              <h3 className="text-xs font-semibold">Group access</h3>
              <div className="divide-y rounded-md border">
                {review.groups.map((group) => {
                  const note = groupNote(group, activation)
                  return (
                    <div key={group.group_id} className="px-3 py-2.5">
                      <div className="flex items-baseline gap-3">
                        <span className="min-w-0 flex-1 text-sm">
                          <span className="font-medium">
                            {group.group_name}
                          </span>
                          {note ? (
                            <span className="text-muted-foreground">
                              {" "}
                              {note}
                            </span>
                          ) : null}
                        </span>
                        <span className="shrink-0 text-xs text-muted-foreground">
                          {groupCounts(group)}
                        </span>
                      </div>
                      {group.takes_over ? <TakeoverNote group={group} /> : null}
                    </div>
                  )
                })}
              </div>
            </section>
          )}

          {moves.map((move) => (
            <CollapsedRow
              key={move.section}
              open={open.has(move.section)}
              onToggle={() => toggle(move.section, [move.people])}
              label={move.label}
              trailing="No access change"
            >
              <PeopleList
                lines={move.people.items.map((person) => ({
                  key: person.user_id,
                  email: person.email,
                  note: (person.groups ?? []).join(", "),
                }))}
                expanded
                loading={loadingAll}
              />
            </CollapsedRow>
          ))}

          {nothingChanges && (
            <p className="text-sm text-muted-foreground">
              No one&apos;s access changes.
            </p>
          )}
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
            disabled={pending || loadingAll || missingLabels}
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
