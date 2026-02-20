import {
  AlarmClockOffIcon,
  CircleCheck,
  CircleMinusIcon,
  CircleX,
  GitForkIcon,
} from "lucide-react"
import type { WorkflowExecutionEventStatus } from "@/client"
import { Spinner } from "@/components/loading/spinner"
import type { WorkflowExecutionEventCompact } from "@/lib/event-history"
import { cn } from "@/lib/utils"

export function getAggregateWorkflowEventStatus(
  relatedEvents: WorkflowExecutionEventCompact[]
): WorkflowExecutionEventStatus {
  const statuses = relatedEvents.map((event) => event.status)

  if (statuses.some((status) => status === "FAILED")) return "FAILED"
  if (statuses.some((status) => status === "TIMED_OUT")) return "TIMED_OUT"
  if (statuses.some((status) => status === "CANCELED")) return "CANCELED"
  if (statuses.some((status) => status === "TERMINATED")) return "TERMINATED"
  if (statuses.some((status) => status === "STARTED")) return "STARTED"
  if (statuses.some((status) => status === "SCHEDULED")) return "SCHEDULED"
  if (statuses.every((status) => status === "COMPLETED")) return "COMPLETED"
  if (statuses.some((status) => status === "DETACHED")) return "DETACHED"

  return "UNKNOWN"
}

export function getWorkflowEventIcon(
  status: WorkflowExecutionEventStatus,
  className?: string
) {
  switch (status) {
    case "SCHEDULED":
      return <Spinner className={cn("!size-3", className)} />
    case "STARTED":
      return <Spinner className={className} />
    case "COMPLETED":
      return (
        <CircleCheck
          className={cn(
            "border-none border-emerald-500 fill-emerald-500 stroke-white",
            className
          )}
        />
      )
    case "FAILED":
      return <CircleX className={cn("fill-rose-500 stroke-white", className)} />
    case "CANCELED":
      return (
        <CircleMinusIcon
          className={cn("fill-orange-500 stroke-white", className)}
        />
      )
    case "TERMINATED":
      return (
        <CircleMinusIcon
          className={cn("fill-rose-500 stroke-white", className)}
        />
      )
    case "TIMED_OUT":
      return (
        <AlarmClockOffIcon
          className={cn("!size-3 stroke-rose-500", className)}
          strokeWidth={2.5}
        />
      )
    case "DETACHED":
      return (
        <GitForkIcon
          className={cn("!size-3 stroke-emerald-500", className)}
          strokeWidth={2.5}
        />
      )
    case "UNKNOWN":
      return <CircleX className={cn("fill-rose-500 stroke-white", className)} />
    default:
      throw new Error("Invalid status")
  }
}
