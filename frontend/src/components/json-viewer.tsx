"use client"

import { CheckCheckIcon, CopyIcon } from "lucide-react"
import React from "react"
import JsonView from "react18-json-view"
import type { NodeMeta } from "react18-json-view/dist/types"
import { Badge } from "@/components/ui/badge"
import { Switch } from "@/components/ui/switch"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { toast } from "@/components/ui/use-toast"
import { cn } from "@/lib/utils"

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
  className?: string
}

export function JsonViewWithControls({
  src,
  defaultExpanded = false,
  defaultTab = "flat",
  showControls = true,
  copyPrefix,
  className,
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
    <div
      className={cn(
        "w-full overflow-x-auto rounded-md border bg-muted-foreground/5",
        className
      )}
      onClick={(e) => e.stopPropagation()}
    >
      <Tabs defaultValue={defaultTab}>
        {showControls && isCollapsible && (
          <div
            className="flex h-7 items-center justify-between gap-4 rounded-t-md border-b pl-2"
            onClick={(e) => e.stopPropagation()}
          >
            <>
              <div
                className="flex items-center gap-2"
                onClick={(e) => e.stopPropagation()}
              >
                <Switch
                  size="xs"
                  checked={isExpanded}
                  onCheckedChange={setIsExpanded}
                  className="data-[state=checked]:bg-muted-foreground"
                  onClick={(e) => e.stopPropagation()}
                />
                <p className="text-xs text-foreground/70">
                  {isExpanded ? "Collapse" : "Expand"}
                </p>
              </div>
            </>
            <TabsList
              className="rounded-b-none border-none bg-transparent text-xs shadow-none"
              onClick={(e) => e.stopPropagation()}
            >
              {tabItems.map(({ value, label }) => (
                <TabsTrigger
                  key={value}
                  value={value}
                  className="border-none bg-transparent text-xs shadow-none data-[state=active]:!bg-transparent data-[state=active]:!shadow-none"
                  onClick={(e) => e.stopPropagation()}
                >
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
            className={cn("p-4", showControls ? "rounded-b-md" : "rounded-md")}
            onClick={(e) => e.stopPropagation()}
          >
            <JsonView
              collapsed={!isExpanded}
              displaySize
              enableClipboard
              src={source ?? null}
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
                      onClick={(e) => {
                        e.stopPropagation()
                        onClick(e)
                      }}
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
