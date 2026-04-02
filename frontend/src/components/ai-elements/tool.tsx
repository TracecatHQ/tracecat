"use client"

import type { DynamicToolUIPart, ToolUIPart } from "ai"
import {
  ChevronDownIcon,
  CircleCheckIcon,
  ClockIcon,
  DownloadIcon,
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

const MAX_INLINE_PAYLOAD_CHARS = 12_000

export type ToolProps = ComponentProps<typeof Collapsible>

export const Tool = ({ className, ...props }: ToolProps) => (
  <Collapsible
    className={cn(
      "group not-prose mb-2 w-full rounded-md border-[0.5px]",
      className
    )}
    {...props}
  />
)

export type ToolPart = ToolUIPart | DynamicToolUIPart
type LegacyToolState =
  | "approval-requested"
  | "approval-responded"
  | "output-denied"
type ToolState = ToolPart["state"] | LegacyToolState

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
  "output-denied": "Error",
  "output-error": "Error",
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
  "output-denied": (
    <XCircleIcon className="size-4 fill-rose-500 stroke-background" />
  ),
  "output-error": (
    <XCircleIcon className="size-4 fill-rose-500 stroke-background" />
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

export const ToolHeader = ({
  className,
  title,
  type,
  state,
  toolName,
  icon,
  ...props
}: ToolHeaderProps) => {
  const derivedName =
    type === "dynamic-tool"
      ? (toolName ?? "dynamic-tool")
      : type.split("-").slice(1).join("-")

  return (
    <CollapsibleTrigger
      className={cn(
        "flex w-full items-center justify-between gap-3 px-2.5 py-1.5",
        className
      )}
      {...props}
    >
      <div className="flex items-center gap-2">
        {icon ?? <WrenchIcon className="size-4 text-muted-foreground" />}
        <span className="font-medium text-sm">{title ?? derivedName}</span>
        {getStatusBadge(state)}
      </div>
      <ChevronDownIcon className="size-4 text-muted-foreground transition-transform group-data-[state=open]:rotate-180" />
    </CollapsibleTrigger>
  )
}

export type ToolContentProps = ComponentProps<typeof CollapsibleContent>

export const ToolContent = ({ className, ...props }: ToolContentProps) => (
  <CollapsibleContent
    className={cn(
      "data-[state=closed]:fade-out-0 data-[state=closed]:slide-out-to-top-2 data-[state=open]:slide-in-from-top-2 space-y-3 px-2.5 pb-3 pt-2.5 text-popover-foreground outline-none data-[state=closed]:animate-out data-[state=open]:animate-in",
      className
    )}
    {...props}
  />
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
      <h4 className="font-medium text-[11px] text-muted-foreground tracking-wide">
        PARAMETERS
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
        <h4 className="font-medium text-[11px] text-muted-foreground tracking-wide">
          {errorText ? "Error" : "RESULT"}
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
