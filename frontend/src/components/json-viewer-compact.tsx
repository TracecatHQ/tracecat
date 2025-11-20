"use client"

import { CheckCheckIcon, CopyIcon } from "lucide-react"
import React from "react"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"

interface CompactJsonViewerProps {
  src: unknown
  className?: string
  maxLength?: number
}

export function CompactJsonViewer({
  src,
  className,
  maxLength = 100,
}: CompactJsonViewerProps): JSX.Element {
  const [copied, setCopied] = React.useState(false)

  const jsonString = React.useMemo(() => {
    if (src === null || src === undefined) {
      return "null"
    }
    try {
      return JSON.stringify(src)
    } catch {
      return String(src)
    }
  }, [src])

  const displayString = React.useMemo(() => {
    if (jsonString.length <= maxLength) {
      return jsonString
    }
    return `${jsonString.slice(0, maxLength)}...`
  }, [jsonString, maxLength])

  const formatJsonWithHighlightedKeys = (value: unknown): React.ReactNode => {
    const jsonStr = JSON.stringify(value, null, 2)
    // Match JSON keys (quoted strings followed by colon)
    const regex = /("[\w-]+")(\s*:)/g
    const parts: React.ReactNode[] = []
    let lastIndex = 0
    let match

    while ((match = regex.exec(jsonStr)) !== null) {
      // Add text before the key
      if (match.index > lastIndex) {
        parts.push(jsonStr.slice(lastIndex, match.index))
      }
      // Add the highlighted key
      parts.push(
        <span key={match.index} className="text-sky-300">
          {match[1]}
        </span>
      )
      // Add the colon
      parts.push(match[2])
      lastIndex = match.index + match[0].length
    }
    // Add remaining text
    if (lastIndex < jsonStr.length) {
      parts.push(jsonStr.slice(lastIndex))
    }
    return parts
  }

  const handleCopy = React.useCallback(async () => {
    try {
      await navigator.clipboard.writeText(jsonString)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch (error) {
      console.error("Failed to copy:", error)
    }
  }, [jsonString])

  const isEmpty =
    src === null ||
    src === undefined ||
    (typeof src === "object" && Object.keys(src as object).length === 0)

  if (isEmpty) {
    return <span className="text-xs text-muted-foreground">No data</span>
  }

  return (
    <div
      className={cn("group flex items-center gap-2 overflow-hidden", className)}
      onClick={(e) => e.stopPropagation()}
    >
      <Tooltip>
        <TooltipTrigger asChild>
          <code className="flex-1 truncate text-xs font-mono text-muted-foreground">
            {displayString}
          </code>
        </TooltipTrigger>
        <TooltipContent className="max-w-[600px]">
          <pre className="max-h-[400px] overflow-auto text-xs">
            {formatJsonWithHighlightedKeys(src)}
          </pre>
        </TooltipContent>
      </Tooltip>
      <button
        onClick={handleCopy}
        className="shrink-0 opacity-0 transition-opacity group-hover:opacity-100"
        aria-label="Copy JSON"
      >
        {copied ? (
          <CheckCheckIcon className="size-3 text-green-600" />
        ) : (
          <CopyIcon className="size-3 text-muted-foreground hover:text-foreground" />
        )}
      </button>
    </div>
  )
}
