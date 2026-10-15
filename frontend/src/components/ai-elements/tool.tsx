"use client"

import type { DynamicToolUIPart, ToolUIPart } from "ai"
import {
  CircleAlertIcon,
  CircleCheckIcon,
  CircleSlashIcon,
  ClockIcon,
  DownloadIcon,
  LoaderCircleIcon,
  WrenchIcon,
  XCircleIcon,
} from "lucide-react"
import {
  type ComponentProps,
  isValidElement,
  memo,
  type ReactNode,
  useEffect,
  useMemo,
  useState,
} from "react"
import { Button } from "@/components/ui/button"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import {
  Trace,
  TraceCollapsible,
  TraceCollapsibleContent,
  TraceCollapsibleTrigger,
  TraceIcon,
  TraceLine,
  type TraceStatus,
  TraceTitle,
} from "@/components/ui/trace"
import { cn } from "@/lib/utils"
import { CodeBlock, CodeBlockCopyButton } from "./code-block"

const MAX_INLINE_PAYLOAD_CHARS = 12_000

export type ToolProps = ComponentProps<typeof TraceCollapsible>

/** A tool call as a Trace line that expands into its input and output. */
export const Tool = ({ className, ...props }: ToolProps) => (
  <Trace className={cn("not-prose", className)}>
    <TraceCollapsible {...props} />
  </Trace>
)

export type ToolPart = ToolUIPart | DynamicToolUIPart
type LegacyToolState =
  | "approval-requested"
  | "approval-responded"
  | "output-denied"
/**
 * Display-only state for tool calls that never completed because the user
 * stopped the turn. Derived at render time; never sent to or by the server.
 */
type InterruptedToolState = "output-interrupted"
export type ToolState =
  | ToolPart["state"]
  | LegacyToolState
  | InterruptedToolState

export type ToolHeaderProps = {
  title?: string
  className?: string
  icon?: ReactNode
  type: ToolPart["type"]
  state: ToolState
  toolName?: string
}

const statusLabels: Record<ToolState, string> = {
  "approval-requested": "Approval required",
  "approval-responded": "Completed",
  "input-available": "In progress",
  "input-streaming": "In progress",
  "output-available": "Completed",
  "output-denied": "Denied",
  "output-error": "Error",
  "output-interrupted": "Interrupted",
}

const statusIcons: Record<ToolState, ReactNode> = {
  "approval-requested": (
    <ClockIcon className="size-3.5 text-amber-500 animate-pulse" />
  ),
  "approval-responded": (
    <CircleCheckIcon className="size-4 fill-emerald-500 stroke-background" />
  ),
  "input-available": (
    <ClockIcon className="size-3.5 text-amber-500 animate-pulse" />
  ),
  "input-streaming": (
    <ClockIcon className="size-3.5 text-amber-500 animate-pulse" />
  ),
  "output-available": (
    <CircleCheckIcon className="size-4 fill-emerald-500 stroke-background" />
  ),
  "output-denied": <BanIcon className="size-3.5 text-muted-foreground" />,
  "output-error": (
    <XCircleIcon className="size-4 fill-rose-500 stroke-background" />
  ),
  "output-interrupted": (
    <CircleSlashIcon className="size-3.5 text-muted-foreground" />
  ),
}

export const getStatusBadge = (status: ToolState) => (
  <TooltipProvider delayDuration={0}>
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          aria-label={statusLabels[status]}
          className="inline-flex items-center"
          role="img"
        >
          {statusIcons[status]}
          <span className="sr-only">{statusLabels[status]}</span>
        </span>
      </TooltipTrigger>
      <TooltipContent side="top">{statusLabels[status]}</TooltipContent>
    </Tooltip>
  </TooltipProvider>
)

/** How a tool state reads on its Trace line. */
export function getToolLineStatus(state: ToolState): {
  status: TraceStatus | undefined
  verb: string
} {
  switch (state) {
    case "input-streaming":
    case "input-available":
      return { status: "running", verb: "Running" }
    case "approval-requested":
      return { status: "pending", verb: "Waiting for approval to run" }
    case "output-error":
      return { status: "error", verb: "Failed to run" }
    case "output-denied":
      return { status: "error", verb: "Denied" }
    case "output-interrupted":
      return { status: "pending", verb: "Stopped" }
    default:
      return { status: undefined, verb: "Ran" }
  }
}

/** The leading icon for a tool line: a spinner or alert overrides the tool's own. */
export function ToolLineIcon({
  status,
  icon,
}: {
  status: TraceStatus | undefined
  icon?: ReactNode
}) {
  if (status === "running") {
    return <LoaderCircleIcon className="animate-spin" />
  }
  if (status === "error") {
    return <CircleAlertIcon />
  }
  return icon ?? <WrenchIcon />
}

export const ToolHeader = ({
  className,
  title,
  type,
  state,
  toolName,
  icon,
}: ToolHeaderProps) => {
  const derivedName =
    type === "dynamic-tool"
      ? (toolName ?? "dynamic-tool")
      : type.split("-").slice(1).join("-")
  const { status, verb } = getToolLineStatus(state)

  return (
    <TraceCollapsibleTrigger className={className}>
      <TraceLine status={status}>
        <TraceIcon>
          <ToolLineIcon status={status} icon={icon} />
        </TraceIcon>
        <TraceTitle>
          {verb} {title ?? derivedName}
        </TraceTitle>
      </TraceLine>
    </TraceCollapsibleTrigger>
  )
}

export type ToolContentProps = ComponentProps<typeof TraceCollapsibleContent>

export const ToolContent = ({ className, ...props }: ToolContentProps) => (
  <TraceCollapsibleContent className={cn("space-y-3", className)} {...props} />
)

type SerializedPayload = {
  text: string
  extension: "json" | "txt"
}

/**
 * Normalize MCP content arrays to the actual payload text when possible.
 * Example: [{ type: "text", text: "[]" }] -> []
 */
function extractMcpTextContent(payload: unknown): unknown {
  if (!Array.isArray(payload) || payload.length === 0) {
    return payload
  }

  const isMcpContentArray = payload.every(
    (item) =>
      item &&
      typeof item === "object" &&
      "type" in item &&
      typeof item.type === "string"
  )

  if (!isMcpContentArray) {
    return payload
  }

  const textBlocks = payload.filter(
    (item): item is { type: "text"; text: string } =>
      item.type === "text" && "text" in item && typeof item.text === "string"
  )

  if (textBlocks.length === 0) {
    return payload
  }

  if (textBlocks.length === 1) {
    try {
      return JSON.parse(textBlocks[0].text)
    } catch {
      return textBlocks[0].text
    }
  }

  return textBlocks.map((block) => {
    try {
      return JSON.parse(block.text)
    } catch {
      return block.text
    }
  })
}

function serializeToolPayload(payload: unknown): SerializedPayload {
  if (typeof payload === "string") {
    const trimmed = payload.trim()
    const looksLikeJson = trimmed.startsWith("{") || trimmed.startsWith("[")
    if (looksLikeJson) {
      if (payload.length > MAX_INLINE_PAYLOAD_CHARS) {
        return { text: payload, extension: "json" }
      }

      try {
        return {
          text: JSON.stringify(JSON.parse(payload), null, 2),
          extension: "json",
        }
      } catch {
        return { text: payload, extension: "json" }
      }
    }
    return { text: payload, extension: "txt" }
  }

  if (
    payload &&
    typeof payload === "object" &&
    !Array.isArray(payload) &&
    isValidElement(payload)
  ) {
    return { text: "", extension: "txt" }
  }

  if (payload && typeof payload === "object") {
    try {
      const compact = JSON.stringify(payload)
      if (typeof compact !== "string") {
        return { text: String(payload), extension: "txt" }
      }

      if (compact.length > MAX_INLINE_PAYLOAD_CHARS) {
        return { text: compact, extension: "json" }
      }

      return {
        text: JSON.stringify(payload, null, 2),
        extension: "json",
      }
    } catch {
      return { text: "[Unserializable payload]", extension: "txt" }
    }
  }

  if (payload === null) {
    return { text: "null", extension: "txt" }
  }

  if (payload === undefined) {
    return { text: "", extension: "txt" }
  }

  return { text: String(payload), extension: "txt" }
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) {
    return `${bytes} B`
  }
  const kb = bytes / 1024
  if (kb < 1024) {
    return `${kb.toFixed(1)} KB`
  }
  const mb = kb / 1024
  return `${mb.toFixed(1)} MB`
}

const ToolPayload = memo(
  function ToolPayload({
    payload,
    downloadName,
  }: {
    payload: unknown
    downloadName: string
  }) {
    const normalizedPayload = useMemo(
      () => extractMcpTextContent(payload),
      [payload]
    )
    const serialized = useMemo(
      () => serializeToolPayload(normalizedPayload),
      [normalizedPayload]
    )
    const isLargePayload = serialized.text.length > MAX_INLINE_PAYLOAD_CHARS
    const codeLanguage = serialized.extension === "json" ? "json" : "console"
    const byteCount = useMemo(
      () =>
        isLargePayload ? new TextEncoder().encode(serialized.text).length : 0,
      [isLargePayload, serialized.text]
    )
    const [downloadHref, setDownloadHref] = useState<string | null>(null)

    useEffect(() => {
      if (!isLargePayload || !serialized.text) {
        setDownloadHref(null)
        return
      }

      const blob = new Blob([serialized.text], {
        type:
          serialized.extension === "json" ? "application/json" : "text/plain",
      })
      const href = URL.createObjectURL(blob)
      setDownloadHref(href)
      return () => URL.revokeObjectURL(href)
    }, [isLargePayload, serialized.extension, serialized.text])

    if (!serialized.text) {
      return null
    }

    if (isLargePayload) {
      return (
        <div className="space-y-2">
          <div className="flex items-center justify-between gap-2 rounded-md border border-dashed bg-background/70 px-2.5 py-2 text-xs text-muted-foreground">
            <span>
              Large payload ({formatBytes(byteCount)}). Preview hidden.
            </span>
            {downloadHref && (
              <Button
                asChild
                variant="outline"
                size="sm"
                className="h-6 px-2 text-xs"
              >
                <a
                  href={downloadHref}
                  download={`${downloadName}.${serialized.extension}`}
                >
                  <DownloadIcon className="mr-1.5 size-3" />
                  Download file
                </a>
              </Button>
            )}
          </div>
        </div>
      )
    }

    return (
      <div className="space-y-2">
        <CodeBlock
          code={serialized.text}
          language={codeLanguage}
          className="[&_code]:text-[11px] [&_pre]:px-3 [&_pre]:py-2.5 [&_pre]:text-[11px]"
        >
          <CodeBlockCopyButton className="absolute right-1.5 top-1.5 z-10 size-5 [&_svg]:size-3" />
        </CodeBlock>
      </div>
    )
  },
  (prev, next) =>
    prev.payload === next.payload && prev.downloadName === next.downloadName
)
ToolPayload.displayName = "ToolPayload"

export type ToolInputProps = ComponentProps<"div"> & {
  input: ToolPart["input"]
}

export const ToolInput = memo(
  ({ className, input, ...props }: ToolInputProps) => (
    <div className={cn("space-y-2 overflow-hidden", className)} {...props}>
      <h4 className="font-medium text-[11px] text-muted-foreground">
        Parameters
      </h4>
      <ToolPayload payload={input} downloadName="tool-parameters" />
    </div>
  )
)
ToolInput.displayName = "ToolInput"

export type ToolOutputProps = ComponentProps<"div"> & {
  output: ToolPart["output"]
  errorText: ToolPart["errorText"]
}

export const ToolOutput = memo(
  ({ className, output, errorText, ...props }: ToolOutputProps) => {
    const hasOutput = output !== undefined && output !== null
    if (!hasOutput && !errorText) {
      return null
    }

    return (
      <div className={cn("space-y-2", className)} {...props}>
        <h4 className="font-medium text-[11px] text-muted-foreground">
          {errorText ? "Error" : "Result"}
        </h4>
        {errorText && (
          <div className="rounded-md bg-destructive/10 px-2.5 py-1.5 text-[11px] text-destructive">
            {errorText}
          </div>
        )}
        {hasOutput &&
          (isValidElement(output) ? (
            <div className="rounded-md bg-muted/50 p-1.5 text-[11px] text-foreground">
              {output}
            </div>
          ) : (
            <ToolPayload payload={output} downloadName="tool-result" />
          ))}
      </div>
    )
  }
)
ToolOutput.displayName = "ToolOutput"
