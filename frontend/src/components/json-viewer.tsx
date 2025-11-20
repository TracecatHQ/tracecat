"use client"

import { CheckCheckIcon, CopyIcon } from "lucide-react"
import React from "react"
import JsonView from "react18-json-view"
import type { NodeMeta } from "react18-json-view/dist/types"
import { Switch } from "@/components/ui/switch"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"

import "react18-json-view/src/style.css"

function isSimpleIdentifier(segment: string): boolean {
  return /^[A-Za-z_][A-Za-z0-9_]*$/.test(segment)
}

function quoteJsonPathKey(segment: string): string {
  const escaped = String(segment).replace(/\\/g, "\\\\").replace(/"/g, '\\"')
  return `"${escaped}"`
}

function escapePathSegment(segment: string): string {
  return isSimpleIdentifier(segment) ? segment : quoteJsonPathKey(segment)
}

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
    const safeKey = escapePathSegment(k)

    if (typeof obj[k] === "object" && obj[k] !== null) {
      if (Array.isArray(obj[k])) {
        ;(obj[k] as unknown[]).forEach((item, index) => {
          const arrayPath = `${safeKey}[${index}]`
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
            pre ? `${pre}${safeKey}` : safeKey
          )
        )
      }
    } else {
      acc[pre ? `${pre}${safeKey}` : safeKey] = obj[k]
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

  // Start from the raw prefix (do not escape/modify it), then append the processed segments
  const seed = prefix ?? ""
  const remainingSegments = path ?? []

  return remainingSegments.reduce((accPath, segment) => {
    const currentSegment = String(segment)

    if (currentSegment.startsWith("[")) {
      // Already bracketed array/index path like [0]
      return `${accPath}${currentSegment}`
    }
    if (isNumeric(currentSegment)) {
      // For numeric segments, use bracket notation
      return `${accPath}[${currentSegment}]`
    }

    const safeSegment = escapePathSegment(currentSegment)

    // If there's no accumulated path yet (no prefix), start with the segment directly
    if (accPath.length === 0) {
      return safeSegment
    }
    return `${accPath}.${safeSegment}`
  }, seed)
}
