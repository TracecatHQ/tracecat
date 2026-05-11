"use client"

import { Clock } from "lucide-react"
import type React from "react"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import type { User } from "@/lib/auth"
import { cn, shortTimeAgo } from "@/lib/utils"

export const CASE_WORKFLOW_TRIGGER_EVENT = "tracecat:open-case-workflow-trigger"

export function UserHoverCard({
  user,
  children,
}: {
  user: User
  children?: React.ReactNode
}) {
  const displayName = user.getDisplayName()
  const avatarText = displayName.substring(0, 1).toUpperCase()

  return (
    <HoverCard>
      <HoverCardTrigger asChild>{children}</HoverCardTrigger>
      <HoverCardContent className="w-auto max-w-xs" side="top">
        <div className="flex items-center gap-3">
          <Avatar className="size-8 shrink-0">
            <AvatarFallback className="text-sm font-medium">
              {avatarText}
            </AvatarFallback>
          </Avatar>
          <div className="flex min-w-0 flex-col">
            <span className="truncate text-sm font-medium">{displayName}</span>
            <span className="truncate text-xs text-muted-foreground">
              {user.email}
            </span>
          </div>
        </div>
      </HoverCardContent>
    </HoverCard>
  )
}
export function CaseUserAvatar({
  user,
  className,
  size = "md",
}: {
  user: User
  className?: string
  size?: "sm" | "md" | "lg"
}) {
  const displayName = user.getDisplayName()
  const avatarText = displayName.substring(0, 1).toUpperCase()
  return (
    <UserHoverCard user={user}>
      <Avatar
        className={cn(
          "cursor-default",
          className,
          size === "sm" && "size-5",
          size === "md" && "size-8",
          size === "lg" && "size-12"
        )}
      >
        <AvatarFallback
          className={cn(
            "text-sm font-medium",
            size === "sm" && "text-[10px]",
            size === "md" && "text-sm",
            size === "lg" && "text-lg"
          )}
        >
          {avatarText}
        </AvatarFallback>
      </Avatar>
    </UserHoverCard>
  )
}

export function CaseEventTimestamp({
  createdAt,
  lastEditedAt,
  showIcon = true,
}: {
  createdAt: string
  lastEditedAt?: string | null
  showIcon?: boolean
}) {
  const createdAtDate = new Date(createdAt)
  const lastEditedAtDate = lastEditedAt ? new Date(lastEditedAt) : undefined

  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger className="cursor-default">
          <span className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
            {showIcon && <Clock className="h-3 w-3 flex-shrink-0" />}
            {shortTimeAgo(createdAtDate)}
            {lastEditedAt && <span className="ml-1">(edited)</span>}
          </span>
        </TooltipTrigger>
        <TooltipContent>
          {createdAtDate.toLocaleString()}
          {lastEditedAtDate && (
            <>
              <br />
              Edited: {lastEditedAtDate.toLocaleString()}
            </>
          )}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}
