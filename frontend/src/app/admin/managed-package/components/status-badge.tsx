"use client"

import type { ManagedRolloutStatus, ManagedTargetStatus } from "@/client"
import { Badge } from "@/components/ui/badge"
import { Spinner } from "@/components/ui/spinner"
import { cn } from "@/lib/utils"
import {
  ACTIVE_ROLLOUT_STATUSES,
  rolloutStatusDisplay,
  type StatusTone,
  targetStatusDisplay,
} from "../managed-package-utils"

const TONE_DOT_CLASSES: Record<StatusTone, string> = {
  neutral: "bg-muted-foreground/50",
  success: "bg-[hsl(var(--success)/0.7)]",
  warning: "bg-amber-500",
  danger: "bg-destructive",
}

function StatusBadge({
  label,
  tone,
  busy,
}: {
  label: string
  tone: StatusTone
  busy?: boolean
}) {
  return (
    <Badge variant="outline" className="gap-1.5 font-normal">
      {busy ? (
        <Spinner className="size-3" />
      ) : (
        <span
          aria-hidden="true"
          className={cn("size-1.5 rounded-full", TONE_DOT_CLASSES[tone])}
        />
      )}
      {label}
    </Badge>
  )
}

/** Badge for a rollout record's status; spins while work is running. */
export function RolloutStatusBadge({
  status,
}: {
  status: ManagedRolloutStatus
}) {
  const { label, tone } = rolloutStatusDisplay(status)
  return (
    <StatusBadge
      label={label}
      tone={tone}
      busy={ACTIVE_ROLLOUT_STATUSES.has(status)}
    />
  )
}

/** Badge for a target's last rollout status. */
export function TargetStatusBadge({ status }: { status: ManagedTargetStatus }) {
  const { label, tone } = targetStatusDisplay(status)
  return <StatusBadge label={label} tone={tone} />
}
