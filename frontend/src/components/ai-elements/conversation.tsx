"use client"

import { ArrowDownIcon } from "lucide-react"
import type { ComponentProps, ReactNode } from "react"
import { useCallback, useLayoutEffect } from "react"
import { StickToBottom, useStickToBottomContext } from "use-stick-to-bottom"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

export type ConversationProps = Omit<
  ComponentProps<typeof StickToBottom>,
  "children"
> & {
  children?: ReactNode
  /**
   * Identity of the transcript on screen. When it changes, the view jumps to
   * the newest message instead of animating there from the old position.
   */
  scrollKey?: string
}

/**
 * Pins a transcript to its end before the browser paints. The library does
 * this from a resize observer one frame later, so a transcript that already
 * has content would otherwise paint once at the top and then jump.
 */
function ConversationScrollAnchor({ scrollKey }: { scrollKey?: string }) {
  const { scrollRef, scrollToBottom } = useStickToBottomContext()

  useLayoutEffect(() => {
    const scrollElement = scrollRef.current
    if (!scrollElement) {
      return
    }
    scrollElement.scrollTop = scrollElement.scrollHeight
    // Also re-engages the bottom lock when the previous transcript was left
    // scrolled up.
    void scrollToBottom("instant")
  }, [scrollKey, scrollRef, scrollToBottom])

  return null
}

export const Conversation = ({
  className,
  children,
  scrollKey,
  ...props
}: ConversationProps) => (
  <StickToBottom
    className={cn("relative flex-1 overflow-y-auto", className)}
    initial="instant"
    resize="smooth"
    role="log"
    {...props}
  >
    {children}
    {/* After the content, so its scroll element is attached first. */}
    <ConversationScrollAnchor scrollKey={scrollKey} />
  </StickToBottom>
)

export type ConversationContentProps = ComponentProps<
  typeof StickToBottom.Content
>

export const ConversationContent = ({
  className,
  ...props
}: ConversationContentProps) => (
  <StickToBottom.Content className={cn("p-4", className)} {...props} />
)

export type ConversationEmptyStateProps = ComponentProps<"div"> & {
  title?: string
  description?: string
  icon?: React.ReactNode
}

export const ConversationEmptyState = ({
  className,
  title = "No messages yet",
  description = "Start a conversation to see messages here",
  icon,
  children,
  ...props
}: ConversationEmptyStateProps) => (
  <div
    className={cn(
      "flex size-full flex-col items-center justify-center gap-3 p-8 text-center",
      className
    )}
    {...props}
  >
    {children ?? (
      <>
        {icon && <div className="text-muted-foreground">{icon}</div>}
        <div className="space-y-1">
          <h3 className="font-medium text-sm">{title}</h3>
          {description && (
            <p className="text-muted-foreground text-sm">{description}</p>
          )}
        </div>
      </>
    )}
  </div>
)

export type ConversationScrollButtonProps = ComponentProps<typeof Button>

export const ConversationScrollButton = ({
  className,
  ...props
}: ConversationScrollButtonProps) => {
  const { isAtBottom, scrollToBottom } = useStickToBottomContext()

  const handleScrollToBottom = useCallback(() => {
    scrollToBottom()
  }, [scrollToBottom])

  return (
    !isAtBottom && (
      <Button
        className={cn(
          "absolute bottom-4 left-[50%] translate-x-[-50%] rounded-full",
          className
        )}
        onClick={handleScrollToBottom}
        size="icon"
        type="button"
        variant="outline"
        {...props}
      >
        <ArrowDownIcon className="size-4" />
      </Button>
    )
  )
}
