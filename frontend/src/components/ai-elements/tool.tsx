"use client"

import type { ToolUIPart } from "ai"
import {
  CircleCheckIcon,
  ChevronDownIcon,
  CircleIcon,
  ClockIcon,
  WrenchIcon,
  XCircleIcon,
} from "lucide-react"
import type { ComponentProps, ReactNode } from "react"
import { Badge } from "@/components/ui/badge"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import { cn } from "@/lib/utils"
import { CodeBlock } from "./code-block"

export type ToolProps = ComponentProps<typeof Collapsible>

export const Tool = ({ className, ...props }: ToolProps) => (
  <Collapsible
    className={cn("not-prose mb-4 w-full rounded-lg border-[0.5px]", className)}
    {...props}
  />
)

export type ToolHeaderProps = {
  title?: string
  type: ToolUIPart["type"]
  state: ToolUIPart["state"]
  className?: string
  icon?: ReactNode
}

const getStatusBadge = (status: ToolUIPart["state"]) => {
  const labels = {
    "input-streaming": "Pending",
    "input-available": "Running",
    "output-available": "Completed",
    "output-error": "Error",
  } as const

  const icons = {
    "input-streaming": <CircleIcon className="size-4 text-muted-foreground" />,
    "input-available": (
      <ClockIcon className="size-4 text-amber-500 animate-pulse" />
    ),
    "output-available": (
      <CircleCheckIcon className="size-4 fill-emerald-500 stroke-white" />
    ),
    "output-error": (
      <XCircleIcon className="size-4 fill-rose-500 stroke-white" />
    ),
  } as const

  return (
    <Badge className="gap-1.5 rounded-lg text-xs" variant="secondary">
      {icons[status]}
      {labels[status]}
    </Badge>
  )
}

export const ToolHeader = ({
  className,
  title,
  type,
  state,
  icon,
  ...props
}: ToolHeaderProps) => (
  <CollapsibleTrigger
    className={cn(
      "flex w-full items-center justify-between gap-4 px-3 py-2",
      className
    )}
    {...props}
  >
    <div className="flex items-center gap-2">
      {icon ?? <WrenchIcon className="size-4 text-muted-foreground" />}
      <span className="font-medium text-sm">
        {title ?? type.split("-").slice(1).join("-")}
      </span>
      {getStatusBadge(state)}
    </div>
    <ChevronDownIcon className="size-4 text-muted-foreground transition-transform group-data-[state=open]:rotate-180" />
  </CollapsibleTrigger>
)

export type ToolContentProps = ComponentProps<typeof CollapsibleContent>

export const ToolContent = ({ className, ...props }: ToolContentProps) => (
  <CollapsibleContent
    className={cn(
      "text-popover-foreground outline-none",
      "data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:slide-in-from-top-2",
      "data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:slide-out-to-top-2",
      className
    )}
    {...props}
  />
)

export type ToolInputProps = ComponentProps<"div"> & {
  input: ToolUIPart["input"]
}

export const ToolInput = ({ className, input, ...props }: ToolInputProps) => (
  <div className={cn("space-y-2 overflow-hidden p-4", className)} {...props}>
    <h4 className="font-medium text-muted-foreground text-xs uppercase tracking-wide">
      Parameters
    </h4>
    <div className="rounded-md bg-muted/50">
      <CodeBlock code={JSON.stringify(input, null, 2)} language="json" />
    </div>
  </div>
)

export type ToolOutputProps = ComponentProps<"div"> & {
  output: ToolUIPart["output"]
  errorText: ToolUIPart["errorText"]
}

export const ToolOutput = ({
  className,
  output,
  errorText,
  ...props
}: ToolOutputProps) => {
  if (!(output || errorText)) {
    return null
  }

  let Output = <div>{output as ReactNode}</div>

  if (typeof output === "object") {
    Output = (
      <CodeBlock code={JSON.stringify(output, null, 2)} language="json" />
    )
  } else if (typeof output === "string") {
    Output = <CodeBlock code={output} language="json" />
  }

  return (
    <div className={cn("space-y-2 p-4", className)} {...props}>
      <h4 className="font-medium text-muted-foreground text-xs uppercase tracking-wide">
        {errorText ? "Error" : "Result"}
      </h4>
      <div
        className={cn(
          "overflow-x-auto rounded-md text-xs [&_table]:w-full",
          errorText
            ? "bg-destructive/10 text-destructive"
            : "bg-muted/50 text-foreground"
        )}
      >
        {errorText && <div>{errorText}</div>}
        {Output}
      </div>
    </div>
  )
}
