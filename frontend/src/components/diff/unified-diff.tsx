import { Fragment } from "react"
import type { DiffSegment, UnifiedDiffRow } from "@/lib/diff"
import { cn } from "@/lib/utils"

/** Props for {@link UnifiedDiff}. */
export interface UnifiedDiffProps {
  /** Rows produced by `computeUnifiedDiff`. */
  rows: UnifiedDiffRow[]
  className?: string
}

/**
 * Render a unified (single column) line diff as a CSS grid: old line number,
 * new line number, `+`/`-` marker, then monospace content. Gutter and marker
 * cells are `select-none` so copying a diff yields code, not line numbers.
 *
 * Deliberately hook-free: no `useTheme`/`resolvedTheme`. Colors come from the
 * `--diff-*` CSS tokens defined in both `:root` and `.dark`, which are correct
 * at first paint (next-themes sets `.dark` pre-hydration) and let this render
 * in jsdom with no provider.
 */
export function UnifiedDiff({ rows, className }: UnifiedDiffProps) {
  return (
    <div
      data-testid="unified-diff"
      className={cn(
        "grid grid-cols-[3rem_3rem_1.25rem_minmax(0,1fr)] font-mono text-xs leading-5",
        className
      )}
    >
      {rows.map((row) => renderRow(row))}
    </div>
  )
}

function renderRow(row: UnifiedDiffRow) {
  if (row.kind === "gap") {
    return (
      <div
        key={row.key}
        className="col-span-4 select-none bg-diff-gutter py-1 text-center text-diff-gutter-foreground"
      >
        {formatHiddenLines(row.hiddenLineCount ?? 0)}
      </div>
    )
  }

  let band = ""
  let marker = ""
  let markerColor = ""
  if (row.kind === "added") {
    band = "bg-diff-added text-diff-added-foreground"
    marker = "+"
    markerColor = "text-diff-marker-added"
  } else if (row.kind === "removed") {
    band = "bg-diff-removed text-diff-removed-foreground"
    marker = "-"
    markerColor = "text-diff-marker-removed"
  }
  // Word-level pairing rewrote this row when it carries unchanged segments;
  // only then does the tight emphasis highlight add information beyond the
  // full-row band.
  const emphasize =
    row.kind !== "unchanged" &&
    row.segments.some((segment) => segment.kind === "unchanged")

  return (
    <Fragment key={row.key}>
      <div className="select-none bg-diff-gutter pr-2 text-right tabular-nums text-diff-gutter-foreground">
        {row.oldLineNumber}
      </div>
      <div className="select-none bg-diff-gutter pr-2 text-right tabular-nums text-diff-gutter-foreground">
        {row.newLineNumber}
      </div>
      <div className={cn("select-none text-center", band, markerColor)}>
        {marker}
      </div>
      <div className={cn("whitespace-pre-wrap break-words pr-2", band)}>
        {row.segments.map((segment, index) =>
          renderLineSegment(segment, index, emphasize)
        )}
      </div>
    </Fragment>
  )
}

/**
 * Render one segment of a line. Unchanged text stays a bare text node; added
 * and removed text use `<ins>`/`<del>` for semantics. Both carry
 * `no-underline`: it removes the default underline on `<ins>` and the default
 * strikethrough on `<del>` — strikethrough is prose-only, since the `-` marker
 * already says "removed" and strikethrough is unreadable on code.
 */
function renderLineSegment(
  segment: DiffSegment,
  index: number,
  emphasize: boolean
) {
  if (segment.kind === "added") {
    return (
      <ins
        key={index}
        data-diff="added"
        className={cn(
          "box-decoration-clone no-underline",
          emphasize && "rounded-sm bg-diff-added-emphasis"
        )}
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
          "box-decoration-clone no-underline",
          emphasize && "rounded-sm bg-diff-removed-emphasis"
        )}
      >
        {segment.value}
      </del>
    )
  }
  return <Fragment key={index}>{segment.value}</Fragment>
}

function formatHiddenLines(count: number): string {
  if (count === 1) {
    return "1 unchanged line hidden"
  }
  return `${count} unchanged lines hidden`
}
