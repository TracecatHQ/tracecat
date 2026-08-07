"use client"

import { Loader2 } from "lucide-react"
import type { ReactNode } from "react"
import { useMemo } from "react"
import { Popover, PopoverAnchor, PopoverContent } from "@/components/ui/popover"
import type { MentionSection } from "@/hooks/use-comment-mentions"
import type { CaretCoordinates } from "@/lib/textarea-caret"
import { cn } from "@/lib/utils"

/**
 * Suggestion list for the `@` mention autocomplete.
 *
 * Anchors to a marker pinned at the `@` trigger so the popover holds still for
 * the whole mention session, and portals out of the comment thread, which clips
 * its own overflow. `open` is fully controlled by the caller, so Radix never
 * dismisses the popover on its own.
 */
export function AgentMentionPopover({
  open,
  caret,
  sections,
  itemCount,
  activeIndex,
  isLoading,
  onSelect,
  children,
}: {
  open: boolean
  caret: CaretCoordinates | undefined
  sections: MentionSection[]
  itemCount: number
  activeIndex: number
  isLoading: boolean
  onSelect: (item: MentionSection["items"][number]) => void
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
        {isLoading ? (
          <div className="flex items-center gap-2 px-2 py-1.5 text-xs text-muted-foreground">
            <Loader2 className="size-3 animate-spin" />
            Loading agents...
          </div>
        ) : null}
        {!isLoading && itemCount === 0 ? (
          <div className="px-2 py-1.5 text-xs text-muted-foreground">
            No agents found
          </div>
        ) : null}
        {!isLoading && itemCount > 0 ? (
          <div className="max-h-52 overflow-y-auto p-1">
            {sectionsWithOffset.map(({ section, startIndex }) => (
              <div key={section.section}>
                <div className="px-2 py-1 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                  {section.label}
                </div>
                {section.items.map((item, index) => (
                  <button
                    key={item.id}
                    type="button"
                    onMouseDown={(event) => event.preventDefault()}
                    onClick={() => onSelect(item)}
                    className={cn(
                      "flex w-full items-center rounded-sm px-2 py-1 text-left",
                      activeIndex === startIndex + index && "bg-accent"
                    )}
                  >
                    <span className="w-full truncate text-xs text-foreground">
                      {item.label}
                    </span>
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
