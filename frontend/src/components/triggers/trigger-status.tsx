import type { SubscriptionStatus, TriggerStatus } from "@/client"
import { cn } from "@/lib/utils"

/** A small dot and label for an online/offline trigger, target or source. */
export function TriggerStatusLabel({ status }: { status: TriggerStatus }) {
  const online = status === "online"
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
      <span
        className={cn(
          "size-1.5 rounded-full",
          online ? "bg-emerald-500" : "bg-muted-foreground/40"
        )}
      />
      {online ? "Online" : "Offline"}
    </span>
  )
}

/** A small dot and label for an event source's provider subscription. */
export function SubscriptionStatusLabel({
  status,
}: {
  status: SubscriptionStatus
}) {
  const confirmed = status === "confirmed"
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
      <span
        className={cn(
          "size-1.5 rounded-full",
          confirmed ? "bg-emerald-500" : "bg-amber-500"
        )}
      />
      {confirmed ? "Subscription confirmed" : "Subscription pending"}
    </span>
  )
}
