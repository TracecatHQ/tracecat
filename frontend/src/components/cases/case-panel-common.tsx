import type React from "react"
import { formatDistanceToNow } from "date-fns"
import { Clock } from "lucide-react"

import { User } from "@/lib/auth"
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

export function CaseUserAvatar({ user }: { user: User }) {
  const displayName = user.getDisplayName()
  const avatarText = displayName.substring(0, 1).toUpperCase()
  const username = user.email.split("@")[0]

  return (
    <HoverCard>
      <HoverCardTrigger asChild>
        <Avatar className="size-8 cursor-default">
          <AvatarFallback className="bg-primary/10 text-xs text-primary">
            {avatarText}
          </AvatarFallback>
        </Avatar>
      </HoverCardTrigger>
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

export function CaseEventTimestemp({
  createdAt,
  lastEditedAt,
}: {
  createdAt: string
  lastEditedAt?: string | null
}) {
  const createdAtDate = new Date(createdAt)
  const lastEditedAtDate = lastEditedAt ? new Date(lastEditedAt) : undefined

  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger className="cursor-default">
          <span className="flex items-center gap-1 text-xs text-muted-foreground">
            <Clock className="size-3" />
            {formatDistanceToNow(createdAtDate, {
              addSuffix: true,
            })}
            {lastEditedAt && <span className="ml-1">(edited)</span>}
          </span>
        </TooltipTrigger>
        <TooltipContent>
          Created: {createdAtDate.toLocaleString()}
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
