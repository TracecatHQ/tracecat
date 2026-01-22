import { BotIcon, CheckCircle2Icon, ClockIcon, XCircleIcon } from "lucide-react"
import type { InboxItemRead, InboxItemStatus } from "@/client"
import { cn } from "@/lib/utils"

interface InboxListItemProps {
  item: InboxItemRead
  isSelected: boolean
  onClick: () => void
}

function StatusIcon({ status }: { status: InboxItemStatus }) {
  switch (status) {
    case "pending":
      return <ClockIcon className="size-3.5 text-amber-500" />
    case "completed":
      return <CheckCircle2Icon className="size-3.5 text-emerald-500" />
    case "failed":
      return <XCircleIcon className="size-3.5 text-red-500" />
    default:
      return null
  }
}

/**
 * Get status text for the inbox item.
 * Shows "Completed", "Failed", or "X approvals remaining" based on status/metadata.
 */
function getStatusText(item: InboxItemRead): string {
  const metadata = item.metadata as
    | { pending_count?: number; total_approvals?: number }
    | undefined

  if (item.status === "failed") {
    return "Failed"
  }

  if (item.status === "completed") {
    return "Completed"
  }

  // Pending status - show number of approvals remaining
  const pendingCount = metadata?.pending_count ?? 0
  if (pendingCount > 0) {
    return `${pendingCount} approval${pendingCount !== 1 ? "s" : ""} remaining`
  }

  return "Running"
}

function formatTimeAgo(dateStr: string): string {
  const date = new Date(dateStr)
  const now = new Date()
  const diffMs = now.getTime() - date.getTime()
  const diffMins = Math.floor(diffMs / 60000)
  const diffHours = Math.floor(diffMs / 3600000)
  const diffDays = Math.floor(diffMs / 86400000)
  const diffWeeks = Math.floor(diffDays / 7)
  const diffMonths = Math.floor(diffDays / 30)

  if (diffMins < 1) return "now"
  if (diffMins < 60) return `${diffMins}m`
  if (diffHours < 24) return `${diffHours}h`
  if (diffDays < 7) return `${diffDays}d`
  if (diffDays < 30) return `${diffWeeks}w`
  return `${diffMonths}mo`
}

export function InboxListItem({
  item,
  isSelected,
  onClick,
}: InboxListItemProps) {
  // Display name: prefer alias, then workflow title, then item title
  const displayName = item.workflow?.alias || item.workflow?.title || item.title

  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "flex w-full items-start gap-3 px-3 py-2.5 text-left transition-colors",
        "hover:bg-muted/50",
        isSelected && "bg-muted"
      )}
    >
      {/* Avatar */}
      <div className="flex size-7 shrink-0 items-center justify-center rounded-full bg-muted-foreground/10">
        <BotIcon className="size-3.5 text-muted-foreground" />
      </div>

      {/* Content */}
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          {item.unread && (
            <span className="size-2 shrink-0 rounded-full bg-blue-500" />
          )}
          <span className="truncate text-sm font-medium">{displayName}</span>
        </div>
        {/* Status text instead of preview */}
        <p className="mt-1 truncate text-xs text-muted-foreground">
          {getStatusText(item)}
        </p>
      </div>

      {/* Right side: status icon + time */}
      <div className="flex shrink-0 flex-col items-end gap-1">
        <StatusIcon status={item.status} />
        <span className="text-[11px] text-muted-foreground">
          {formatTimeAgo(item.created_at)}
        </span>
      </div>
    </button>
  )
}
