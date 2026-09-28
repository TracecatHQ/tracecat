"use client"

import { AlertTriangle } from "lucide-react"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { getSessionLastError, type SessionOrChat } from "@/lib/chat"
import { cn } from "@/lib/utils"

interface ChatLastErrorIndicatorProps {
  /**
   * A session/chat row. Either arm of the session/chat union; the error is read
   * off the AgentSession arms, so legacy chats (no last_error) render nothing.
   */
  session: SessionOrChat
  className?: string
}

/**
 * Compact indicator for a session whose most recent run errored.
 *
 * Renders nothing when there is no persisted error, so it is safe to drop into
 * any chat-list row. The full error reason is shown on hover rather than inline
 * to keep list rows scannable.
 */
export function ChatLastErrorIndicator({
  session,
  className,
}: ChatLastErrorIndicatorProps) {
  const lastError = getSessionLastError(session)
  if (!lastError) {
    return null
  }

  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <AlertTriangle
            className={cn("size-3.5 shrink-0 text-destructive", className)}
            aria-label="Last run failed"
          />
        </TooltipTrigger>
        <TooltipContent className="max-w-xs">
          <p className="text-xs font-medium">Last run failed</p>
          <p className="mt-1 whitespace-pre-wrap break-words text-xs text-muted-foreground">
            {lastError}
          </p>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}
