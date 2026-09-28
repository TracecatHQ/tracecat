import { Fragment } from "react"
import { type DiffSegment, splitSegmentsIntoParagraphs } from "@/lib/diff"
import { cn } from "@/lib/utils"

/** Props for {@link ProseDiff}. */
export interface ProseDiffProps {
  /** Word-level diff segments covering the whole document. */
  segments: DiffSegment[]
  className?: string
}

/**
 * Render a word-level prose diff as flowing sans-serif text, split into
 * paragraphs on blank lines. Added text is highlighted, removed text is
 * struck through, and there are no gutters or line numbers.
 *
 * Deliberately hook-free: no `useTheme`/`resolvedTheme`. Colors come from the
 * `--diff-*` CSS tokens defined in both `:root` and `.dark`, which are correct
 * at first paint (next-themes sets `.dark` pre-hydration) and let this render
 * in jsdom with no provider.
 */
export function ProseDiff({ segments, className }: ProseDiffProps) {
  const paragraphs = splitSegmentsIntoParagraphs(segments)
  return (
    <div
      data-testid="prose-diff"
      className={cn("space-y-4 font-sans text-sm leading-7", className)}
    >
      {paragraphs.map((paragraph) => (
        <p key={paragraph.key} className="whitespace-pre-wrap break-words">
          {paragraph.segments.map((segment, index) =>
            renderProseSegment(segment, index, paragraph.segments[index + 1])
          )}
        </p>
      ))}
    </div>
  )
}

/**
 * Render one prose segment. Unchanged text stays a bare text node; added and
 * removed text use `<ins>`/`<del>` for semantics. Additions receive the strong
 * diff highlight; deletions are explicitly struck through and muted. A small
 * gap separates an adjacent delete/insert replacement pair.
 */
function renderProseSegment(
  segment: DiffSegment,
  index: number,
  nextSegment?: DiffSegment
) {
  if (segment.kind === "added") {
    return (
      <ins
        key={index}
        data-diff="added"
        className="rounded-sm bg-[hsl(var(--diff-added-emphasis))] px-0.5 py-px box-decoration-clone text-[hsl(var(--diff-added-foreground))] no-underline"
      >
        {segment.value}
      </ins>
    )
  }
  if (segment.kind === "removed") {
    return (
      <del
        key={index}
        data-diff="removed"
        className={cn(
          "text-muted-foreground line-through decoration-muted-foreground",
          nextSegment?.kind === "added" && "mr-1"
        )}
      >
        {segment.value}
      </del>
    )
  }
  return <Fragment key={index}>{segment.value}</Fragment>
}
