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

export function UserHoverCard({
  user,
  children,
}: {
  user: User
  children?: React.ReactNode
}) {
  const displayName = user.getDisplayName()
  const avatarText = displayName.substring(0, 1).toUpperCase()
  const username = user.email.split("@")[0]

  return (
    <HoverCard>
      <HoverCardTrigger asChild>{children}</HoverCardTrigger>
      <HoverCardContent className="w-auto" side="top">
        <div className="flex items-center gap-4">
          <Avatar className="size-16">
            <AvatarFallback className="bg-primary/10 text-lg text-primary">
              {avatarText}
            </AvatarFallback>
          </Avatar>
          <div className="flex flex-col gap-1">
            <div className="flex items-center gap-2">
              <span className="text-base font-medium">{displayName}</span>
              <span className="text-muted-foreground">({username})</span>
              {user.role && (
                <span className="rounded-md bg-primary/10 px-2 py-0.5 text-xs font-medium capitalize text-primary">
                  {user.role}
                </span>
              )}
            </div>
            <span className="text-xs text-muted-foreground">{user.email}</span>
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
          size === "sm" && "size-4",
          size === "md" && "size-8",
          size === "lg" && "size-12"
        )}
      >
        <AvatarFallback
          className={cn(
            "bg-primary/10 text-primary",
            size === "sm" && "text-xs",
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
