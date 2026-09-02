"use client"

import { Loader2, Lock } from "lucide-react"
import type { ReactNode } from "react"
import { useMemo } from "react"
import { Popover, PopoverAnchor, PopoverContent } from "@/components/ui/popover"
import type { MentionSection, MentionSuggestion } from "@/hooks/use-mentions"
import type { MentionKind } from "@/lib/mentions"
import type { CaretCoordinates } from "@/lib/textarea-caret"
import { cn } from "@/lib/utils"

const POPOVER_COPY: Record<
  MentionKind,
  { loading: string; empty: string; locked: string; error: string }
> = {
  agent: {
    loading: "Loading agents...",
    empty: "No agents found",
    locked: "Agent mentions are an Enterprise feature",
    error: "Could not load agents. Try again.",
  },
  workflow: {
    loading: "Loading workflows...",
    empty: "No workflows found",
    locked: "Workflow commands are an Enterprise feature",
    error: "Could not load workflows. Try again.",
  },
}

/**
 * Suggestion list for the `@` agent and `/` workflow autocomplete.
 *
 * Anchors to a marker pinned at the trigger character so the popover holds
 * still for the whole mention session, and portals out of containers that clip
 * their own overflow, such as the comment thread. `open` is fully controlled by
 * the caller, so Radix never dismisses the popover on its own.
 *
 * An org without the entitlement still gets the popover, showing a single inert
 * Enterprise row instead of suggestions. It is deliberately not a dialog
 * trigger: the user opened this by typing a character, not by clicking.
 */
export function MentionPopover({
  open,
  kind,
  caret,
  sections,
  itemCount,
  activeIndex,
  isLoading,
  locked = false,
  hasError = false,
  onSelect,
  children,
}: {
  open: boolean
  /** Which trigger opened the popover; picks the loading and empty copy. */
  kind: MentionKind | undefined
  caret: CaretCoordinates | undefined
  sections: MentionSection[]
  itemCount: number
  activeIndex: number
  isLoading: boolean
  /** Show the Enterprise lock row instead of suggestions. */
  locked?: boolean
  /** Say the lookup failed rather than claiming there is nothing to show. */
  hasError?: boolean
  onSelect: (item: MentionSuggestion) => void
  children: ReactNode
}) {
  // Flatten section offsets so each row knows its index in the keyboard order.
  const sectionsWithOffset = useMemo(() => {
    let offset = 0
    return sections.map((section) => {
      const startIndex = offset
      offset += section.items.length
      return { section, startIndex }
    })
  }, [sections])
  const copy = POPOVER_COPY[kind ?? "agent"]

  // One discriminant rather than a guard per branch: the states are mutually
  // exclusive, and re-testing `isLoading` and `locked` in every arm is how the
  // next state added turns into eight terms.
  let view: "loading" | "locked" | "error" | "empty" | "list"
  if (isLoading) {
    view = "loading"
  } else if (locked) {
    view = "locked"
  } else if (hasError) {
    view = "error"
  } else if (itemCount === 0) {
    view = "empty"
  } else {
    view = "list"
  }

  return (
    <Popover open={open}>
      <div className="relative">
        {children}
        <PopoverAnchor asChild>
          <span
            aria-hidden
            className="pointer-events-none absolute w-px"
            style={{
              top: caret?.top ?? 0,
              left: caret?.left ?? 0,
              height: caret?.height ?? 0,
            }}
          />
        </PopoverAnchor>
      </div>
      <PopoverContent
        portal
        side="top"
        align="start"
        sideOffset={4}
        collisionPadding={8}
        // Focus stays in the composer so typing keeps filtering the list.
        onOpenAutoFocus={(event) => event.preventDefault()}
        className="w-64 overflow-hidden p-0"
      >
        {view === "loading" ? (
          <div className="flex items-center gap-2 px-2 py-1.5 text-xs text-muted-foreground">
            <Loader2 className="size-3 animate-spin" />
            {copy.loading}
          </div>
        ) : null}
        {view === "locked" ? (
          <div className="flex items-center gap-2 px-2 py-1.5 text-xs text-muted-foreground">
            <Lock className="size-3 shrink-0" />
            {copy.locked}
          </div>
        ) : null}
        {view === "error" ? (
          <div className="px-2 py-1.5 text-xs text-muted-foreground">
            {copy.error}
          </div>
        ) : null}
        {view === "empty" ? (
          <div className="px-2 py-1.5 text-xs text-muted-foreground">
            {copy.empty}
          </div>
        ) : null}
        {view === "list" ? (
          <div className="max-h-52 overflow-y-auto p-1">
            {sectionsWithOffset.map(({ section, startIndex }) => (
              <div key={section.section}>
                <div className="px-2 py-1 text-[10px] font-medium text-muted-foreground">
                  {section.label}
                </div>
                {section.items.map((item, index) => (
                  <button
                    key={item.id}
                    type="button"
                    onMouseDown={(event) => event.preventDefault()}
                    onClick={() => onSelect(item)}
                    className={cn(
                      "flex w-full items-center gap-2 rounded-sm px-2 py-1 text-left",
                      activeIndex === startIndex + index && "bg-accent"
                    )}
                  >
                    <span className="min-w-0 flex-1 truncate text-xs text-foreground">
                      {item.label}
                    </span>
                    {item.hint ? (
                      <span className="max-w-[45%] truncate text-[10px] text-muted-foreground">
                        {item.hint}
                      </span>
                    ) : null}
                  </button>
                ))}
              </div>
            ))}
          </div>
        ) : null}
      </PopoverContent>
    </Popover>
  )
}
