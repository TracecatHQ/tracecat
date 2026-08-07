"use client"

import { Loader2 } from "lucide-react"
import type { AgentPresetReadMinimal } from "@/client"
import { cn } from "@/lib/utils"

/**
 * Suggestion list for the `@` agent mention autocomplete.
 *
 * Positioned absolutely, so the caller must render it inside a `relative`
 * container anchored to the composer input.
 */
export function AgentMentionPopover({
  suggestions,
  activeIndex,
  query,
  isLoading,
  onSelect,
}: {
  suggestions: AgentPresetReadMinimal[]
  activeIndex: number
  query: string
  isLoading: boolean
  onSelect: (preset: AgentPresetReadMinimal) => void
}) {
  return (
    <div className="absolute inset-x-0 top-full z-30 mt-1">
      <div className="overflow-hidden rounded-md border bg-popover shadow-md">
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
      </div>
    </div>
  )
}
