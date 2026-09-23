"use client"

import { KeyRoundIcon, UserMinusIcon, UserPlusIcon } from "lucide-react"
import type { ReactNode } from "react"
import type { ScimActivationReviewRead, ScimMappingPlanRead } from "@/client"
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"

function plural(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`
}

function unionSize(lists: string[][]): number {
  return new Set(lists.flat()).size
}

function SummaryLine({
  icon,
  children,
}: {
  icon: ReactNode
  children: ReactNode
}) {
  return (
    <p className="flex items-center gap-2.5 text-sm">
      {icon}
      <span>{children}</span>
    </p>
  )
}

function MemberList({
  title,
  rows,
}: {
  title: string
  rows: { id: string; email: string; note: string }[]
}) {
  return (
    <div className="space-y-1">
      <p className="text-xs font-medium text-muted-foreground">{title}</p>
      <ul className="space-y-0.5">
        {rows.map((row) => (
          <li key={row.id} className="flex justify-between gap-4 text-sm">
            <span className="truncate">{row.email}</span>
            <span className="shrink-0 text-xs text-muted-foreground">
              {row.note}
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}

function PlanItem({ plan }: { plan: ScimMappingPlanRead }) {
  const losing = new Set(plan.users_losing_access)
  const email = (id: string) =>
    plan.manual_member_emails?.[id] ?? "User details unavailable"
  const lose = plan.users_losing_access.map((id) => ({
    id,
    email: email(id),
    note: "Added manually, not in the IdP group",
  }))
  const kept = plan.manual_members_purged
    .filter((id) => !losing.has(id))
    .map((id) => ({
      id,
      email: email(id),
      note: "Keeps access through the IdP group",
    }))
  const gaining = plan.users_gaining_access.length

  return (
    <AccordionItem
      value={`${plan.external_group_id}-${plan.group_id}`}
      className="border-b last:border-b-0"
    >
      <AccordionTrigger className="gap-3 px-4 py-3 hover:no-underline">
        <span className="flex-1 text-left">
          {plan.external_group_display_name} → {plan.group_name}
        </span>
        <span className="text-xs font-normal text-muted-foreground">
          +{gaining} gain
        </span>
        {plan.manual_members_purged.length > 0 && (
          <span className="text-xs font-normal text-muted-foreground">
            {plan.manual_members_purged.length} manual removed
          </span>
        )}
        {lose.length > 0 && (
          <span className="text-xs font-semibold">
            {plural(lose.length, "loses", "lose")} access
          </span>
        )}
      </AccordionTrigger>
      <AccordionContent className="space-y-3 px-4 pl-11">
        {lose.length > 0 && (
          <MemberList title={`Loses ${plan.group_name} access`} rows={lose} />
        )}
        {kept.length > 0 && (
          <MemberList
            title="Manual members replaced by IdP membership"
            rows={kept}
          />
        )}
        {lose.length === 0 && kept.length === 0 && (
          <p className="text-sm text-muted-foreground">
            {plural(gaining, "user gains", "users gain")} access. No manual
            members change.
          </p>
        )}
      </AccordionContent>
    </AccordionItem>
  )
}

/** Summarize what activation or a new mapping changes, then confirm it. */
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
  const skipped = review.users.length - joining.length
  const gainCount = unionSize(review.plans.map((p) => p.users_gaining_access))
  const loseCount = unionSize(review.plans.map((p) => p.users_losing_access))
  const missingLabels = review.plans.some((plan) =>
    plan.manual_members_purged.some((id) => !plan.manual_member_emails?.[id])
  )
  const groupCount = new Set(review.plans.map((plan) => plan.group_id)).size
  const firstPlan = review.plans[0]

  const confirmLabel = activation
    ? `Activate for ${plural(joining.length, "user", "users")}`
    : "Apply mapping"

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !pending) onClose()
      }}
    >
      <DialogContent className="max-h-[85vh] max-w-xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>
            {activation ? "Activate SCIM provisioning" : "Review group mapping"}
          </DialogTitle>
          <DialogDescription>
            {activation
              ? `Your identity provider takes over membership for ${plural(groupCount, "group", "groups")}.`
              : `Your identity provider takes over membership of ${firstPlan?.group_name ?? "this group"}.`}{" "}
            Access from other roles and groups is unchanged.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-2">
          {activation && (
            <SummaryLine
              icon={
                <UserPlusIcon className="size-4 shrink-0 text-muted-foreground" />
              }
            >
              <strong className="font-semibold">{joining.length}</strong>{" "}
              {joining.length === 1 ? "user joins" : "users join"} the
              organization
              {skipped > 0 && (
                <span className="text-muted-foreground">
                  {" "}
                  · {skipped} inactive skipped
                </span>
              )}
            </SummaryLine>
          )}
          <SummaryLine
            icon={
              <KeyRoundIcon className="size-4 shrink-0 text-muted-foreground" />
            }
          >
            <strong className="font-semibold">{gainCount}</strong>{" "}
            {gainCount === 1 ? "user gains" : "users gain"} group access
          </SummaryLine>
          {loseCount > 0 && (
            <SummaryLine icon={<UserMinusIcon className="size-4 shrink-0" />}>
              <strong className="font-semibold">{loseCount}</strong>{" "}
              {loseCount === 1 ? "user loses" : "users lose"} group access
              <span className="text-muted-foreground"> · listed below</span>
            </SummaryLine>
          )}
        </div>

        {(review.plans.length > 0 || activation) && (
          <Accordion
            type="multiple"
            defaultValue={review.plans
              .filter((plan) => plan.users_losing_access.length > 0)
              .map((plan) => `${plan.external_group_id}-${plan.group_id}`)}
            className="rounded-lg border"
          >
            {review.plans.map((plan) => (
              <PlanItem
                key={`${plan.external_group_id}-${plan.group_id}`}
                plan={plan}
              />
            ))}
            {activation && (
              <AccordionItem value="users" className="border-b-0">
                <AccordionTrigger className="gap-3 px-4 py-3 hover:no-underline">
                  <span className="flex-1 text-left">
                    Users joining the organization
                  </span>
                  <span className="text-xs font-normal text-muted-foreground">
                    {joining.length} join · {skipped} skipped · pending invites
                    revoked
                  </span>
                </AccordionTrigger>
                <AccordionContent className="px-4 pl-11">
                  {review.users.length === 0 ? (
                    <p className="text-sm text-muted-foreground">
                      No users have been pushed yet.
                    </p>
                  ) : (
                    <MemberList
                      title="Pushed users"
                      rows={review.users.map((user) => ({
                        id: user.id,
                        email: user.email,
                        note: user.active ? "Joins" : "Inactive, skipped",
                      }))}
                    />
                  )}
                </AccordionContent>
              </AccordionItem>
            )}
          </Accordion>
        )}

        {missingLabels && (
          <p role="alert" className="text-sm text-destructive">
            Affected user details could not be loaded. Close this review and try
            again before confirming.
          </p>
        )}

        <DialogFooter className="items-center gap-2 sm:justify-between">
          <span className="text-xs text-muted-foreground">
            Uses the directory as of confirmation.
          </span>
          <div className="flex gap-2">
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
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
