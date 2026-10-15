"use client"

import { ChevronRightIcon } from "lucide-react"
import type { ComponentProps } from "react"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import { cn } from "@/lib/utils"

// Ported from the shadcn/ui Trace component (radix) to Tailwind 3.

/** A list of lines for the steps an agent takes. */
function Trace({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      data-slot="trace"
      className={cn(
        "flex w-full min-w-0 flex-col gap-2",
        // Links and buttons that wrap a line take the line's shape.
        "[&>*:has(>[data-slot=trace-line])]:flex [&>*:has(>[data-slot=trace-line])]:w-fit [&>*:has(>[data-slot=trace-line])]:min-w-0 [&>*:has(>[data-slot=trace-line])]:max-w-full [&>*:has(>[data-slot=trace-line])]:rounded-md [&>*:has(>[data-slot=trace-line])]:text-left [&>*:has(>[data-slot=trace-line])]:outline-none [&>*:has(>[data-slot=trace-line]):focus-visible]:ring-1 [&>*:has(>[data-slot=trace-line]):focus-visible]:ring-ring",
        className
      )}
      {...props}
    />
  )
}

/** Status of a step. Unset means it finished. */
type TraceStatus = "running" | "error" | "pending"

/** One step: an icon, a title, and an optional description. */
function TraceLine({
  className,
  status,
  ...props
}: ComponentProps<"span"> & { status?: TraceStatus }) {
  return (
    <span
      data-slot="trace-line"
      data-status={status}
      className={cn(
        "flex w-fit min-w-0 max-w-full items-center gap-2 text-sm text-muted-foreground transition-colors",
        "[:is(a,button)>&]:cursor-pointer [:is(a,button):hover>&]:text-foreground",
        "data-[status=error]:text-destructive [:is(a,button):hover>&]:data-[status=error]:text-destructive",
        "data-[status=pending]:opacity-60",
        "[&[data-status=running]>[data-slot=trace-title]]:text-shimmer",
        className
      )}
      {...props}
    />
  )
}

function TraceIcon({ className, ...props }: ComponentProps<"span">) {
  return (
    <span
      data-slot="trace-icon"
      aria-hidden="true"
      className={cn(
        "flex size-4 shrink-0 items-center justify-center [&_svg:not([class*='size-'])]:size-4 [&_svg]:pointer-events-none [[data-slot=trace-title]~&]:-ml-1",
        className
      )}
      {...props}
    />
  )
}

function TraceTitle({ className, ...props }: ComponentProps<"span">) {
  return (
    <span
      data-slot="trace-title"
      className={cn("min-w-0 truncate", className)}
      {...props}
    />
  )
}

function TraceDescription({ className, ...props }: ComponentProps<"span">) {
  return (
    <span
      data-slot="trace-description"
      className={cn(
        "shrink-0 tabular-nums text-muted-foreground/70",
        className
      )}
      {...props}
    />
  )
}

/** A line that expands into its detail or into nested lines. */
function TraceCollapsible({
  className,
  ...props
}: ComponentProps<typeof Collapsible>) {
  return (
    <Collapsible
      data-slot="trace-collapsible"
      className={cn("flex w-full min-w-0 flex-col", className)}
      {...props}
    />
  )
}

function TraceCollapsibleTrigger({
  className,
  children,
  ...props
}: Omit<ComponentProps<typeof CollapsibleTrigger>, "asChild">) {
  return (
    <CollapsibleTrigger
      data-slot="trace-collapsible-trigger"
      className={cn(
        "group/trace-trigger flex w-fit min-w-0 max-w-full cursor-pointer items-center gap-1 rounded-md text-left text-muted-foreground outline-none transition-colors hover:text-foreground focus-visible:ring-1 focus-visible:ring-ring has-[[data-status=error]]:text-destructive",
        className
      )}
      {...props}
    >
      {children}
      <TraceIcon className="transition-transform group-aria-expanded/trace-trigger:rotate-90">
        <ChevronRightIcon />
      </TraceIcon>
    </CollapsibleTrigger>
  )
}

function TraceCollapsibleContent({
  className,
  children,
  ...props
}: Omit<ComponentProps<typeof CollapsibleContent>, "asChild">) {
  return (
    <CollapsibleContent
      data-slot="trace-collapsible-content"
      className="min-w-0"
      {...props}
    >
      <div
        data-slot="trace-collapsible-content-inner"
        className={cn(
          "mb-1 ml-[calc(0.5rem-1px)] mt-2 min-w-0 break-words border-l pl-4 pr-1 leading-relaxed text-muted-foreground",
          className
        )}
      >
        {children}
      </div>
    </CollapsibleContent>
  )
}

export {
  Trace,
  TraceLine,
  TraceIcon,
  TraceTitle,
  TraceDescription,
  TraceCollapsible,
  TraceCollapsibleTrigger,
  TraceCollapsibleContent,
  type TraceStatus,
}
