"use client"

import { Loader2 } from "lucide-react"
import type { ReactNode } from "react"
import type { AgentPresetReadMinimal } from "@/client"
import { Popover, PopoverAnchor, PopoverContent } from "@/components/ui/popover"
import { cn } from "@/lib/utils"

/**
 * Suggestion list for the `@` agent mention autocomplete.
 *
 * Renders above `children` (the composer input it anchors to) and portals out
 * of the comment thread, which clips its own overflow. `open` is fully
 * controlled by the caller, so Radix never dismisses the popover on its own.
 */
export function AgentMentionPopover({
  open,
  suggestions,
  activeIndex,
  query,
  isLoading,
  onSelect,
  children,
}: {
  open: boolean
  suggestions: AgentPresetReadMinimal[]
  activeIndex: number
  query: string
  isLoading: boolean
  onSelect: (preset: AgentPresetReadMinimal) => void
  children: ReactNode
}) {
  return (
    <Popover open={open}>
      <PopoverAnchor asChild>{children}</PopoverAnchor>
      <PopoverContent
        portal
        side="top"
        align="start"
        sideOffset={4}
        collisionPadding={8}
        // Focus stays in the composer so typing keeps filtering the list.
        onOpenAutoFocus={(event) => event.preventDefault()}
        className="w-[var(--radix-popover-trigger-width)] overflow-hidden p-0"
      >
        {isLoading ? (
          <div className="flex items-center gap-2 px-3 py-2 text-xs text-muted-foreground">
            <Loader2 className="size-3 animate-spin" />
            Loading agents...
          </div>
        ) : null}
        {!isLoading && suggestions.length === 0 ? (
          <div className="px-3 py-2 text-xs text-muted-foreground">
            No agents found for
            {` "${query}"`}.
          </div>
        ) : null}
        {!isLoading && suggestions.length > 0 ? (
          <div className="max-h-64 overflow-y-auto p-1">
            {suggestions.map((preset, index) => (
              <button
                key={preset.id}
                type="button"
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => onSelect(preset)}
                className={cn(
                  "flex w-full flex-col items-start gap-0.5 rounded-sm px-2 py-1.5 text-left",
                  activeIndex === index && "bg-accent"
                )}
              >
                <span className="w-full truncate text-xs font-medium text-foreground">
                  {preset.name}
                </span>
                {preset.description ? (
                  <span className="w-full truncate text-[11px] text-muted-foreground">
                    {preset.description}
                  </span>
                ) : null}
              </button>
            ))}
          </div>
        ) : null}
      </PopoverContent>
    </Popover>
  )
}
