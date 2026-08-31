"use client"

import { buildMentionSegments, type MentionRange } from "@/lib/mentions"
import { cn } from "@/lib/utils"

/**
 * Backdrop that paints the composer's text so mentions can be highlighted.
 *
 * The textarea above it renders its own text transparent, so this layer is what
 * the user actually reads. That only works while the two lay text out
 * identically: the caller passes the textarea's own typography and padding
 * classes through `className`, and Tailwind's preflight makes the textarea
 * inherit the page font, so both boxes share font, size, line height and
 * wrapping.
 *
 * Mentions are tinted but keep the surrounding font weight on purpose. Bolding
 * them would change glyph widths, which shifts every following character and
 * desynchronises the native caret from the text the user sees. The same goes
 * for padding, so the highlight is widened with a ring (a box-shadow spread)
 * that takes no layout space.
 *
 * The composer auto-grows and pins `overflow-y: hidden`, so the textarea never
 * scrolls and this layer needs no scroll synchronisation.
 */
export function MentionOverlay({
  text,
  mentions,
  className,
}: {
  text: string
  mentions: MentionRange[]
  className?: string
}) {
  const segments = buildMentionSegments(text, mentions)

  return (
    <div
      aria-hidden
      data-testid="mention-overlay"
      className={cn(
        "pointer-events-none absolute inset-0 whitespace-pre-wrap break-words text-foreground",
        className
      )}
    >
      {segments.map((segment) =>
        segment.mention ? (
          <span
            key={segment.start}
            data-mention-kind={segment.mention.kind}
            data-mention-target={segment.mention.targetId}
            className="bg-primary/10 text-primary ring-2 ring-primary/10"
          >
            {segment.text}
          </span>
        ) : (
          <span key={segment.start}>{segment.text}</span>
        )
      )}
    </div>
  )
}
