"use client"

import type { ToolUIPart } from "ai"
import {
  ChevronDownIcon,
  CircleCheckIcon,
  CircleIcon,
  ClockIcon,
  WrenchIcon,
  XCircleIcon,
} from "lucide-react"
import type { ComponentProps, ReactNode } from "react"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"
import { CodeBlock, CodeBlockCopyButton } from "./code-block"

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
    "input-streaming": (
      <CircleIcon className="size-3.5 text-muted-foreground animate-pulse" />
    ),
    "input-available": (
      <ClockIcon className="size-3.5 text-amber-500 animate-pulse" />
    ),
    "output-available": (
      <CircleCheckIcon className="size-4 fill-emerald-500 stroke-white" />
    ),
    "output-error": (
      <XCircleIcon className="size-4 fill-rose-500 stroke-white" />
    ),
  } as const

  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <div className="inline-flex">{icons[status]}</div>
        </TooltipTrigger>
        <TooltipContent side="top">
          <p>{labels[status]}</p>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
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
      <CodeBlock code={JSON.stringify(input, null, 2)} language="json">
        <CodeBlockCopyButton />
      </CodeBlock>
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
  const { message, details } = extractErrorDetails(output, errorText)
  const hasError = Boolean(message)

  if (!(output || hasError)) {
    return null
  }

  const outputRecord =
    output && typeof output === "object" && !Array.isArray(output)
      ? (output as Record<string, unknown>)
      : undefined
  const outputIsOnlyErrorText =
    hasError &&
    outputRecord &&
    Object.keys(outputRecord).every((key) => key === "errorText")

  const shouldRenderRawOutput = output && !outputIsOnlyErrorText

  let Output: ReactNode = null
  if (shouldRenderRawOutput) {
    if (
      typeof output === "string" &&
      hasError &&
      message &&
      output.trim() === message.trim()
    ) {
      Output = null
    } else if (typeof output === "object") {
      // Handle MCP content array format: [{"type": "text", "text": "..."}]
      // Extract and parse the text content for pretty display
      const displayOutput = extractMcpTextContent(output)
      Output = (
        <CodeBlock
          code={JSON.stringify(displayOutput, null, 2)}
          language="json"
        >
          <CodeBlockCopyButton />
        </CodeBlock>
      )
    } else if (typeof output === "string") {
      // Try to parse JSON strings for pretty display
      let displayOutput: string
      try {
        displayOutput = JSON.stringify(JSON.parse(output), null, 2)
      } catch {
        displayOutput = output
      }
      Output = (
        <CodeBlock code={displayOutput} language="json">
          <CodeBlockCopyButton />
        </CodeBlock>
      )
    } else {
      Output = <div>{output as ReactNode}</div>
    }
  }

  return (
    <div className={cn("space-y-2 p-4", className)} {...props}>
      <h4 className="font-medium text-muted-foreground text-xs uppercase tracking-wide">
        {hasError ? "Error" : "Result"}
      </h4>
      {hasError && (
        <Alert
          variant="destructive"
          className="border-destructive/30 bg-destructive/10 py-3"
        >
          <AlertTitle className="text-sm font-semibold">
            {details?.title ?? "Validation issue"}
          </AlertTitle>
          <AlertDescription className="mt-1 space-y-2 text-sm">
            <p className="whitespace-pre-wrap text-destructive">
              {details?.summary ?? message}
            </p>
            {details?.items && details.items.length > 0 && (
              <ul className="list-disc space-y-1 pl-5 text-destructive">
                {details.items.map((item, idx) => (
                  <li key={`${item}-${idx}`}>{item}</li>
                ))}
              </ul>
            )}
          </AlertDescription>
        </Alert>
      )}
      {Output && (
        <div className="overflow-x-auto rounded-md bg-muted/50 text-foreground text-xs [&_table]:w-full">
          {Output}
        </div>
      )}
    </div>
  )
}

type ErrorDetails = {
  message?: string
  details?: {
    title: string
    summary?: string
    items?: string[]
  }
}

function extractErrorDetails(
  output: ToolUIPart["output"],
  explicitErrorText: ToolUIPart["errorText"]
): ErrorDetails {
  const outputRecord =
    output && typeof output === "object" && !Array.isArray(output)
      ? (output as Record<string, unknown>)
      : undefined
  const derivedErrorText =
    outputRecord && "errorText" in outputRecord
      ? (outputRecord.errorText as string | undefined)
      : undefined
  const embeddedError =
    (explicitErrorText ?? derivedErrorText)?.trim() || undefined

  if (!embeddedError) {
    return { message: undefined }
  }

  const parsed = parseValidationSummary(embeddedError)
  if (parsed) {
    return { message: embeddedError, details: parsed }
  }

  return {
    message: embeddedError,
    details: {
      title: "Error",
      summary: embeddedError,
    },
  }
}

function parseValidationSummary(message: string):
  | {
      title: string
      summary?: string
      items?: string[]
    }
  | undefined {
  const trimmed = message.trim()
  const validationMatch = trimmed.match(
    /^(\d+)\s+validation errors:\s*(\[.*\])\s*Fix the errors and try again\.\s*$/s
  )
  if (validationMatch) {
    const count = Number(validationMatch[1])
    const title =
      count === 1 ? "Validation error" : `${count} validation errors`
    try {
      const payload = JSON.parse(validationMatch[2]) as Array<{
        loc?: string[] | string
        msg?: string
        input?: unknown
      }>
      const items = payload.map((error) => {
        const path = Array.isArray(error.loc)
          ? error.loc.join(" → ")
          : (error.loc ?? "")
        const prefix = path ? `${path}: ` : ""
        const formattedInput =
          error.input === undefined
            ? ""
            : ` (input: ${
                typeof error.input === "string"
                  ? error.input
                  : JSON.stringify(error.input, null, 2)
              })`
        return `${prefix}${error.msg ?? "Invalid value"}${formattedInput}`
      })
      return {
        title,
        summary:
          "The request couldn't be completed because some fields failed validation.",
        items,
      }
    } catch {
      return {
        title,
        summary: trimmed,
      }
    }
  }

  const feedbackMatch = trimmed.match(/^Validation feedback:\s*(.*)$/s)
  if (feedbackMatch) {
    return {
      title: "Validation feedback",
      summary: feedbackMatch[1].trim(),
    }
  }

  return undefined
}

/**
 * Extract and parse text content from MCP content array format.
 * MCP returns: [{"type": "text", "text": "{...json...}"}]
 * This extracts the text and parses it if it's JSON.
 */
function extractMcpTextContent(output: unknown): unknown {
  if (!Array.isArray(output)) {
    return output
  }

  // Check for MCP content array format
  const firstItem = output[0]
  if (
    firstItem &&
    typeof firstItem === "object" &&
    "type" in firstItem &&
    firstItem.type === "text" &&
    "text" in firstItem &&
    typeof firstItem.text === "string"
  ) {
    // Try to parse the text as JSON
    try {
      return JSON.parse(firstItem.text)
    } catch {
      // Not JSON, return as-is
      return firstItem.text
    }
  }

  // Not MCP format, return original
  return output
}
