"use client"

import { LockKeyholeIcon, UnlinkIcon } from "lucide-react"
import type { DetachedCopyRead, ManagedResourceRead } from "@/client"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { formatRelative } from "@/lib/time"
import { cn } from "@/lib/utils"

/** Props for {@link ManagedBadge}. */
export interface ManagedBadgeProps {
  managed: ManagedResourceRead
  /** Show only the lock icon, for dense rows such as table cells. */
  compact?: boolean
  className?: string
}

/**
 * "Managed by Tracecat" badge. The tooltip shows the package tag the resource
 * was deployed from and when the last rollout wrote it.
 */
export function ManagedBadge({
  managed,
  compact = false,
  className,
}: ManagedBadgeProps) {
  const deployedAt = new Date(managed.deployed_at)
  const relative = formatRelative(deployedAt)
  const absolute = Number.isNaN(deployedAt.getTime())
    ? null
    : deployedAt.toLocaleString()

  return (
    <TooltipProvider delayDuration={200}>
      <Tooltip>
        <TooltipTrigger asChild>
          <span
            role="note"
            aria-label="Managed by Tracecat"
            className={cn(
              "inline-flex shrink-0 items-center gap-1 rounded-md border px-1.5 py-0.5 text-xs font-medium text-muted-foreground",
              compact && "border-transparent px-0.5",
              className
            )}
          >
            <LockKeyholeIcon className="size-3" />
            {!compact && <span>Managed by Tracecat</span>}
          </span>
        </TooltipTrigger>
        <TooltipContent className="max-w-xs space-y-1">
          <p className="font-medium">Managed by Tracecat</p>
          <p>Deployed from tag {managed.deployed_tag}</p>
          {relative && (
            <p title={absolute ?? undefined}>Last rollout {relative}</p>
          )}
          <p>Detach to edit.</p>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}

/** Props for {@link DetachedNote}. */
export interface DetachedNoteProps {
  detached: DetachedCopyRead
  className?: string
}

/** Small note on a copy made by detaching a managed resource. */
export function DetachedNote({ detached, className }: DetachedNoteProps) {
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center gap-1 text-xs text-muted-foreground",
        className
      )}
    >
      <UnlinkIcon className="size-3" />
      Detached from {detached.original_name}
    </span>
  )
}
