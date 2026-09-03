"use client"

import { useMemo } from "react"
import { cn } from "@/lib/utils"

type DiffRowType = "add" | "remove" | "context" | "hunk" | "meta"

interface DiffRow {
  type: DiffRowType
  oldNumber: number | null
  newNumber: number | null
  text: string
}

const HUNK_HEADER = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/

/**
 * Parse a unified diff string (as produced by Python's `difflib.unified_diff`)
 * into renderable rows with old/new line numbers.
 */
function parseUnifiedDiff(diff: string): DiffRow[] {
  const rows: DiffRow[] = []
  let oldNumber = 0
  let newNumber = 0

  for (const line of diff.split("\n")) {
    // File headers are redundant with the source path shown above the diff.
    if (line.startsWith("--- ") || line.startsWith("+++ ")) {
      continue
    }

    const hunk = HUNK_HEADER.exec(line)
    if (hunk) {
      oldNumber = Number(hunk[1])
      newNumber = Number(hunk[2])
      rows.push({ type: "hunk", oldNumber: null, newNumber: null, text: line })
      continue
    }

    const marker = line[0]
    const text = line.slice(1)
    if (marker === "+") {
      rows.push({ type: "add", oldNumber: null, newNumber: newNumber++, text })
    } else if (marker === "-") {
      rows.push({
        type: "remove",
        oldNumber: oldNumber++,
        newNumber: null,
        text,
      })
    } else if (marker === " ") {
      rows.push({
        type: "context",
        oldNumber: oldNumber++,
        newNumber: newNumber++,
        text,
      })
    } else if (line.length > 0) {
      // e.g. the "... diff truncated ..." marker the backend appends.
      rows.push({ type: "meta", oldNumber: null, newNumber: null, text: line })
    }
  }

  return rows
}

const LINE_STYLES: Record<
  "add" | "remove" | "context",
  { row: string; gutter: string; sign: string }
> = {
  add: {
    row: "bg-green-50 dark:bg-green-950/40",
    gutter: "text-green-700 dark:text-green-400",
    sign: "+",
  },
  remove: {
    row: "bg-red-50 dark:bg-red-950/40",
    gutter: "text-red-700 dark:text-red-400",
    sign: "-",
  },
  context: {
    row: "",
    gutter: "text-muted-foreground/60",
    sign: " ",
  },
}

interface UnifiedDiffProps {
  /** Unified diff string from `PullResourceDiff.diff`. */
  diff: string
  className?: string
}

/**
 * Minimal unified (single-column) diff renderer.
 *
 * Renders the unified diff string the backend already computes, rather than
 * recomputing a diff from the before/after file contents on the client. Gives
 * full control over gutter and marker widths that the previous table-based
 * library could not.
 */
export function UnifiedDiff({ diff, className }: UnifiedDiffProps) {
  const rows = useMemo(() => parseUnifiedDiff(diff), [diff])

  return (
    <div
      className={cn("min-w-0 font-mono text-[12px] leading-[1.6]", className)}
    >
      {rows.map((row, index) => {
        if (row.type === "hunk") {
          return (
            <div
              key={`hunk-${index}`}
              className="bg-muted/50 px-3 py-0.5 text-[11px] text-muted-foreground"
            >
              {row.text}
            </div>
          )
        }
        if (row.type === "meta") {
          return (
            <div
              key={`meta-${index}`}
              className="px-3 py-0.5 text-[11px] italic text-muted-foreground"
            >
              {row.text}
            </div>
          )
        }
        const style = LINE_STYLES[row.type]
        return (
          <div
            key={`${row.type}-${index}`}
            className={cn("flex items-start", style.row)}
          >
            <span className="w-9 shrink-0 select-none px-1.5 text-right text-[11px] tabular-nums text-muted-foreground/60">
              {row.oldNumber ?? ""}
            </span>
            <span className="w-9 shrink-0 select-none px-1.5 text-right text-[11px] tabular-nums text-muted-foreground/60">
              {row.newNumber ?? ""}
            </span>
            <span
              className={cn(
                "w-4 shrink-0 select-none text-center",
                style.gutter
              )}
            >
              {style.sign}
            </span>
            <span className="min-w-0 flex-1 whitespace-pre-wrap break-words pr-3">
              {row.text === "" ? " " : row.text}
            </span>
          </div>
        )
      })}
    </div>
  )
}
