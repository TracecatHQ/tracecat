"use client"

import React from "react"
import { CheckCheckIcon, CopyIcon } from "lucide-react"
import JsonView from "react18-json-view"
import { NodeMeta } from "react18-json-view/dist/types"

import { cn } from "@/lib/utils"
import { Badge } from "@/components/ui/badge"
import { Switch } from "@/components/ui/switch"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { toast } from "@/components/ui/use-toast"

import "react18-json-view/src/style.css"

function flattenObject(
  obj: Record<string, unknown> | unknown[],
  prefix = ""
): Record<string, unknown> {
  // Handle root level array
  if (Array.isArray(obj)) {
    return obj.reduce((acc: Record<string, unknown>, item, index) => {
      const arrayPath = `[${index}]`
      if (typeof item === "object" && item !== null) {
        Object.assign(
          acc,
          flattenObject(
            item as Record<string, unknown>,
            prefix ? `${prefix}${arrayPath}` : arrayPath
          )
        )
      } else {
        acc[prefix ? `${prefix}${arrayPath}` : arrayPath] = item
      }
      return acc
    }, {})
  }

  // Original object handling
  return Object.keys(obj).reduce((acc: Record<string, unknown>, k: string) => {
    const pre = prefix.length ? `${prefix}.` : ""

    if (typeof obj[k] === "object" && obj[k] !== null) {
      if (Array.isArray(obj[k])) {
        ;(obj[k] as unknown[]).forEach((item, index) => {
          const arrayPath = `${k}[${index}]`
          if (typeof item === "object" && item !== null) {
            Object.assign(
              acc,
              flattenObject(
                item as Record<string, unknown>,
                pre ? `${pre}${arrayPath}` : arrayPath
              )
            )
          } else {
            acc[pre ? `${pre}${arrayPath}` : arrayPath] = item
          }
        })
      } else {
        Object.assign(
          acc,
          flattenObject(
            obj[k] as Record<string, unknown>,
            pre ? `${pre}${k}` : k
          )
        )
      }
    } else {
      acc[pre ? `${pre}${k}` : k] = obj[k]
    }
    return acc
  }, {})
}

type JsonViewWithControlsTabs = "flat" | "nested"
interface JsonViewWithControlsProps {
  src: unknown
  defaultExpanded?: boolean
  defaultTab?: JsonViewWithControlsTabs
  showControls?: boolean
  copyPrefix?: string
}

export function JsonViewWithControls({
  src,
  defaultExpanded = false,
  defaultTab = "flat",
  showControls = true,
  copyPrefix,
}: JsonViewWithControlsProps): JSX.Element {
  const [isExpanded, setIsExpanded] = React.useState(defaultExpanded)

  // Memoize the source data to prevent unnecessary re-renders
  const memoizedSrc = React.useMemo(() => {
    const isCollapsible = ["object", "array"].includes(typeof src)
    const flattenedSrc =
      typeof src === "object" && src !== null
        ? flattenObject(src as Record<string, unknown>)
        : src

    return {
      isCollapsible,
      flattenedSrc,
      originalSrc: src,
    }
  }, [src])

  const { isCollapsible, flattenedSrc, originalSrc } = memoizedSrc

  const tabItems = React.useMemo(
    () =>
      [
        { value: "flat", label: "Flat", src: flattenedSrc },
        { value: "nested", label: "Nested", src: originalSrc },
      ] as { value: JsonViewWithControlsTabs; label: string; src: unknown }[],
    [flattenedSrc, originalSrc]
  )

  return (
    <div className="w-full space-y-2 overflow-x-auto">
      <Tabs defaultValue={defaultTab}>
        {showControls && isCollapsible && (
          <div className="flex items-center justify-between gap-4">
            <div>
              <div className="flex items-center gap-2">
                <Switch
                  checked={isExpanded}
                  onCheckedChange={setIsExpanded}
                  className="data-[state=checked]:bg-muted-foreground"
                />
                <p className="text-xs text-foreground/70">
                  {isExpanded ? "Collapse" : "Expand"}
                </p>
              </div>
            </div>
            <TabsList className="h-7 text-xs">
              {tabItems.map(({ value, label }) => (
                <TabsTrigger key={value} value={value} className="text-xs">
                  {label}
                </TabsTrigger>
              ))}
            </TabsList>
          </div>
        )}
        {tabItems.map(({ value, src: source }) => (
          <TabsContent
            key={value}
            value={value}
            className="rounded-md border bg-muted-foreground/5 p-4"
          >
            <JsonView
              collapsed={!isExpanded}
              displaySize
              enableClipboard
              src={source}
              className="break-all text-xs"
              theme="atom"
              CopyComponent={({ onClick, className }) => (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <CopyIcon
                      className={cn(
                        "m-0 size-3 p-0 text-muted-foreground",
                        className
                      )}
                      onClick={onClick}
                    />
                  </TooltipTrigger>
                  <TooltipContent>Copy JSONPath</TooltipContent>
                </Tooltip>
              )}
              CopiedComponent={({ className, style }) => (
                <CheckCheckIcon
                  className={cn("text-muted-foreground", className)}
                  style={style}
                />
              )}
              customizeCopy={(
                node: unknown,
                nodeMeta: NodeMeta | undefined
              ) => {
                const { currentPath } = nodeMeta || {}
                const copyValue = buildJsonPath(currentPath || [], copyPrefix)

                toast({
                  title: "Copied JSONPath to clipboard",
                  description: (
                    <Badge
                      variant="secondary"
                      className="bg-muted-foreground/10 font-mono text-xs font-normal tracking-tight"
                    >
                      {copyValue}
                    </Badge>
                  ),
                })
                return copyValue
              }}
            />
          </TabsContent>
        ))}
      </Tabs>
    </div>
  )
}

function isNumeric(str: string): boolean {
  return /^\d+$/.test(str)
}
function buildJsonPath(path: string[], prefix?: string): string | undefined {
  // Combine the arrays
  if (path.length === 0 && !prefix) {
    return undefined
  }
  const allSegments = []
  if (prefix) {
    allSegments.push(prefix)
  }
  if (path.length > 0) {
    allSegments.push(...path)
  }
  return allSegments.reduce((path, segment, index) => {
    // Convert segment to string for type safety
    const currentSegment = String(segment)

    // Handle different cases
    if (isNumeric(currentSegment)) {
      // For numeric segments, use bracket notation
      return `${path}[${currentSegment}]`
    } else if (currentSegment.startsWith("[")) {
      // For array segments, use bracket notation
      return `${path}${currentSegment}`
    } else {
      // For string segments, use dot notation unless it's the first segment
      return index === 0 ? currentSegment : `${path}.${currentSegment}`
    }
  }, "")
}
