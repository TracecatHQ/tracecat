import { ClockPlusIcon, LayersPlusIcon, PencilLineIcon } from "lucide-react"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"

/** Compact relative time used in list rows, e.g. `now`, `5m`, `6h`, `3d`, `2w`, `4mo`, `1y`. */
export function compactTimeAgo(date: Date): string {
  const timestamp = date.getTime()
  if (Number.isNaN(timestamp)) return "-"
  const diffMs = Date.now() - timestamp
  const diffMins = Math.floor(diffMs / 60000)
  const diffHours = Math.floor(diffMs / 3600000)
  const diffDays = Math.floor(diffMs / 86400000)

  if (diffMins < 1) return "now"
  if (diffMins < 60) return `${diffMins}m`
  if (diffHours < 24) return `${diffHours}h`
  if (diffDays < 7) return `${diffDays}d`
  if (diffDays < 30) return `${Math.floor(diffDays / 7)}w`
  if (diffDays < 365) return `${Math.floor(diffDays / 30)}mo`
  return `${Math.floor(diffDays / 365)}y`
}

function RowTimestamp({
  icon: Icon,
  label,
  value,
}: {
  icon: typeof ClockPlusIcon
  label: string
  value: string
}) {
  const date = new Date(value)
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="flex shrink-0 items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
            <Icon className="size-3" />
            {compactTimeAgo(date)}
          </span>
        </TooltipTrigger>
        <TooltipContent>
          {label}: {date.toLocaleString()}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}

export function CreatedAtTimestamp({ createdAt }: { createdAt: string }) {
  return <RowTimestamp icon={ClockPlusIcon} label="Created" value={createdAt} />
}

export function UpdatedAtTimestamp({ updatedAt }: { updatedAt: string }) {
  return (
    <RowTimestamp icon={PencilLineIcon} label="Updated" value={updatedAt} />
  )
}

export function PublishedAtTimestamp({ publishedAt }: { publishedAt: string }) {
  return (
    <RowTimestamp icon={LayersPlusIcon} label="Published" value={publishedAt} />
  )
}

/** Created/updated (and optionally published) timestamps shown at the end of list rows (cases, workflows, skills, ...). */
export function RowTimestamps({
  createdAt,
  updatedAt,
  publishedAt,
  className,
}: {
  createdAt: string
  updatedAt: string
  publishedAt?: string | null
  className?: string
}) {
  return (
    <div className={cn("flex shrink-0 items-center gap-2", className)}>
      <CreatedAtTimestamp createdAt={createdAt} />
      <UpdatedAtTimestamp updatedAt={updatedAt} />
      {publishedAt && <PublishedAtTimestamp publishedAt={publishedAt} />}
    </div>
  )
}
