"use client"

import { useControllableState } from "@radix-ui/react-use-controllable-state"
import type { ComponentProps } from "react"
import {
  createContext,
  memo,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react"
import {
  Trace,
  TraceCollapsible,
  TraceCollapsibleContent,
  TraceCollapsibleTrigger,
  TraceLine,
  TraceTitle,
} from "@/components/ui/trace"
import { useSmoothText } from "@/hooks/use-smooth-text"
import { cn } from "@/lib/utils"
import { Response } from "./response"

type ReasoningContextValue = {
  isStreaming: boolean
  duration: number
}

const ReasoningContext = createContext<ReasoningContextValue | null>(null)

const useReasoning = () => {
  const context = useContext(ReasoningContext)
  if (!context) {
    throw new Error("Reasoning components must be used within Reasoning")
  }
  return context
}

export type ReasoningProps = ComponentProps<typeof TraceCollapsible> & {
  isStreaming?: boolean
  duration?: number
}

const MS_IN_S = 1000
// Treat the user as "pinned" when within this many px of the bottom.
const STICK_TO_BOTTOM_THRESHOLD_PX = 16

export const Reasoning = memo(
  ({
    className,
    isStreaming = false,
    defaultOpen = true,
    duration: durationProp,
    children,
    ...props
  }: ReasoningProps) => {
    const [duration, setDuration] = useControllableState({
      prop: durationProp,
      defaultProp: 0,
    })
    const [startTime, setStartTime] = useState<number | null>(null)

    // Track duration when streaming starts and ends
    useEffect(() => {
      if (isStreaming) {
        if (startTime === null) {
          setStartTime(Date.now())
        }
      } else if (startTime !== null) {
        setDuration(Math.ceil((Date.now() - startTime) / MS_IN_S))
        setStartTime(null)
      }
    }, [isStreaming, startTime, setDuration])

    return (
      <ReasoningContext.Provider value={{ isStreaming, duration }}>
        <Trace className={cn("not-prose", className)}>
          <TraceCollapsible defaultOpen={defaultOpen} {...props}>
            {children}
          </TraceCollapsible>
        </Trace>
      </ReasoningContext.Provider>
    )
  }
)

export type ReasoningTriggerProps = ComponentProps<
  typeof TraceCollapsibleTrigger
>

function getThoughtTitle(isStreaming: boolean, duration?: number): string {
  if (isStreaming) {
    return "Thinking"
  }
  if (!duration) {
    return "Thought"
  }
  return `Thought for ${duration} ${duration === 1 ? "second" : "seconds"}`
}

export const ReasoningTrigger = memo(
  ({ className, children, ...props }: ReasoningTriggerProps) => {
    const { isStreaming, duration } = useReasoning()

    return (
      <TraceCollapsibleTrigger className={className} {...props}>
        {children ?? (
          <TraceLine status={isStreaming ? "running" : undefined}>
            <TraceTitle>{getThoughtTitle(isStreaming, duration)}</TraceTitle>
          </TraceLine>
        )}
      </TraceCollapsibleTrigger>
    )
  }
)

export type ReasoningContentProps = ComponentProps<
  typeof TraceCollapsibleContent
> & {
  children: string
}

export const ReasoningContent = memo(
  ({ className, children, ...props }: ReasoningContentProps) => {
    const { isStreaming } = useReasoning()
    // Reveal reasoning deltas at a steady frame-aligned rate so they don't
    // appear in uneven network-sized bursts while thinking streams in.
    const shownText = useSmoothText(children, isStreaming)
    const scrollRef = useRef<HTMLDivElement>(null)
    // Stay pinned to the latest reasoning unless the user scrolls away.
    const pinnedToBottomRef = useRef(true)

    const handleScroll = useCallback(() => {
      const el = scrollRef.current
      if (!el) {
        return
      }
      const distanceFromBottom =
        el.scrollHeight - el.scrollTop - el.clientHeight
      pinnedToBottomRef.current =
        distanceFromBottom <= STICK_TO_BOTTOM_THRESHOLD_PX
    }, [])

    // Follow streaming deltas by scrolling to the bottom while pinned.
    useEffect(() => {
      if (!isStreaming || !pinnedToBottomRef.current) {
        return
      }
      const el = scrollRef.current
      if (el) {
        el.scrollTop = el.scrollHeight
      }
    }, [children, shownText, isStreaming])

    return (
      <TraceCollapsibleContent className={cn("text-sm", className)} {...props}>
        <div
          ref={scrollRef}
          onScroll={handleScroll}
          data-slot="reasoning-content"
          className="max-h-60 overflow-y-auto overscroll-contain pr-1"
        >
          <Response className="grid gap-2">{shownText}</Response>
        </div>
      </TraceCollapsibleContent>
    )
  }
)

Reasoning.displayName = "Reasoning"
ReasoningTrigger.displayName = "ReasoningTrigger"
ReasoningContent.displayName = "ReasoningContent"
