"use client"

import type { ScimActivationReviewRead } from "@/client"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"

/** Show directory eligibility and the manual memberships a mapping will remove. */
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
  const actionLabel = activation ? "Confirm activation" : "Confirm mapping"
  const missingLabels = review.plans.some((plan) =>
    plan.manual_members_purged.some((id) => !plan.manual_member_emails?.[id])
  )
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !pending) onClose()
      }}
    >
      <DialogContent className="max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>
            {activation ? "Review SCIM activation" : "Review group mapping"}
          </DialogTitle>
          <DialogDescription>
            {activation
              ? "Active directory users will be admitted and their pending invitations revoked. Inactive users will not be admitted. "
              : ""}
            Mapping transfers group membership management to your identity
            provider. Manual memberships listed below will be removed, and
            directory members will gain this group&apos;s access. Other
            independent grants are preserved.
          </DialogDescription>
        </DialogHeader>
        {activation && (
          <ul className="max-h-48 overflow-auto text-sm">
            {review.users.map((user) => (
              <li key={user.id}>
                {user.email} —{" "}
                {user.active ? "Eligible for admission" : "Inactive; skipped"}
              </li>
            ))}
            {review.users.length === 0 && (
              <li>No users have been pushed yet.</li>
            )}
          </ul>
        )}
        {review.plans.map((plan) => (
          <div
            key={`${plan.group_id}-${plan.external_group_id}`}
            className="space-y-2 text-sm"
          >
            <p className="font-medium">
              {plan.external_group_display_name} → {plan.group_name}
            </p>
            <p>
              {plan.manual_members_purged.length} manual memberships will be
              removed.
            </p>
            <ul>
              {plan.manual_members_purged.map((id) => (
                <li key={id}>
                  {plan.manual_member_emails?.[id] ??
                    "User details unavailable"}
                </li>
              ))}
            </ul>
            <p>
              {plan.users_gaining_access?.length ?? 0} directory users will gain
              access through this group.
            </p>
            <p>
              {plan.users_losing_access?.length ?? 0} users will lose access
              through this group.
            </p>
            <ul>
              {(plan.users_losing_access ?? []).map((id) => (
                <li key={id}>
                  {plan.manual_member_emails?.[id] ??
                    "User details unavailable"}
                </li>
              ))}
            </ul>
          </div>
        ))}
        {missingLabels && (
          <p role="alert" className="text-sm text-destructive">
            Affected user details could not be loaded. Close this review and try
            again before confirming.
          </p>
        )}
        <p className="text-sm text-muted-foreground">
          Confirmation uses the latest directory. These changes affect group
          membership; access through other roles or groups is preserved.
        </p>
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
            {pending ? "Applying…" : actionLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
