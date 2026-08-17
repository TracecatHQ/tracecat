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
            renderProseSegment(segment, index)
          )}
        </p>
      ))}
    </div>
  )
}

/**
 * Render one prose segment. Unchanged text stays a bare text node; added and
 * removed text use `<ins>`/`<del>` for semantics. `box-decoration-clone` keeps
 * rounded corners on each line fragment when a highlight wraps, and
 * `no-underline` on `<ins>` stops the browser default underline from fighting
 * the highlight. `<del>` keeps its default strikethrough: strikethrough is
 * prose-only.
 */
function renderProseSegment(segment: DiffSegment, index: number) {
  if (segment.kind === "added") {
    return (
      <ins
        key={index}
        data-diff="added"
        className="rounded-sm bg-diff-added-emphasis box-decoration-clone text-diff-added-foreground no-underline"
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
        className="rounded-sm bg-diff-removed-emphasis box-decoration-clone text-diff-removed-foreground"
      >
        {segment.value}
      </del>
    )
  }
  return <Fragment key={index}>{segment.value}</Fragment>
}
