import { GitBranch } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { compressActionsInString } from "@/lib/expressions"
import { cn } from "@/lib/utils"

/** Longest single-clause condition that still renders inline in the pill. */
const INLINE_CONDITION_MAX_LENGTH = 40

const OPEN_BRACKETS = "([{"
const CLOSE_BRACKETS = ")]}"

/** Top-level boolean operator that joins two `run_if` clauses. */
export type RunIfOperator = "&&" | "||"

/** One line of a stacked `run_if` condition. */
export interface RunIfClause {
  operator: RunIfOperator | null
  text: string
}

/**
 * Strip the `${{ ... }}` template wrapper from a `run_if` expression and
 * compress `ACTIONS.<ref>.result` references for display.
 */
export function formatRunIfCondition(runIf: string): string {
  const trimmed = runIf.trim()
  const inner =
    trimmed.startsWith("${{") && trimmed.endsWith("}}")
      ? trimmed.slice(3, -2).trim()
      : trimmed
  return compressActionsInString(inner)
}

function isWrappedInParens(text: string): boolean {
  if (!text.startsWith("(") || !text.endsWith(")")) {
    return false
  }
  let depth = 0
  let quote: string | null = null
  for (let i = 0; i < text.length; i++) {
    const char = text[i]
    if (quote) {
      if (char === "\\") {
        i++
      } else if (char === quote) {
        quote = null
      }
      continue
    }
    if (char === "'" || char === '"') {
      quote = char
    } else if (char === "(") {
      depth++
    } else if (char === ")") {
      depth--
      if (depth === 0 && i < text.length - 1) {
        return false
      }
    }
  }
  return depth === 0
}

/**
 * Split a formatted condition into clauses on top-level `&&` / `||`.
 *
 * Operators inside strings, brackets, or parentheses are left untouched, and a
 * condition fully wrapped in parentheses is unwrapped before splitting.
 */
export function splitRunIfCondition(condition: string): RunIfClause[] {
  const trimmed = condition.trim()
  const clauses: RunIfClause[] = []
  let depth = 0
  let quote: string | null = null
  let start = 0
  let operator: RunIfOperator | null = null

  for (let i = 0; i < trimmed.length; i++) {
    const char = trimmed[i]
    if (quote) {
      if (char === "\\") {
        i++
      } else if (char === quote) {
        quote = null
      }
      continue
    }
    if (char === "'" || char === '"') {
      quote = char
    } else if (OPEN_BRACKETS.includes(char)) {
      depth++
    } else if (CLOSE_BRACKETS.includes(char)) {
      depth = Math.max(0, depth - 1)
    } else if (depth === 0) {
      const pair = trimmed.slice(i, i + 2)
      if (pair === "&&" || pair === "||") {
        clauses.push({ operator, text: trimmed.slice(start, i).trim() })
        operator = pair
        start = i + 2
        i++
      }
    }
  }
  clauses.push({ operator, text: trimmed.slice(start).trim() })

  if (clauses.length === 1 && isWrappedInParens(trimmed)) {
    return splitRunIfCondition(trimmed.slice(1, -1))
  }
  return clauses
}

/**
 * Whether an expanded `run_if` badge renders as a multi-line stack rather
 * than a single inline pill.
 */
export function isStackedRunIf(runIf: string): boolean {
  const condition = formatRunIfCondition(runIf)
  return (
    condition.length > INLINE_CONDITION_MAX_LENGTH ||
    splitRunIfCondition(condition).length > 1
  )
}

/**
 * Badge shown above an action node that has a `run_if` condition.
 *
 * In compact mode only the condition icon is shown until the action is
 * hovered or selected. Short single-clause conditions expand inline. Longer
 * ones grow the same teal badge upward into one unwrapped clause per line,
 * scrolling past a bounded width and height.
 */
export function RunIfBadge({
  runIf,
  compact,
  expanded,
  attachedToJoin,
}: {
  runIf: string
  compact: boolean
  expanded: boolean
  attachedToJoin?: boolean
}) {
  const condition = formatRunIfCondition(runIf)
  const clauses = splitRunIfCondition(condition)
  const showCondition = !compact || expanded
  const stacked = showCondition && isStackedRunIf(runIf)

  return (
    <Badge
      aria-label={`Run if ${condition}`}
      data-state={showCondition ? "expanded" : "collapsed"}
      className={cn(
        "border-0 text-xs shadow-none",
        "bg-teal-500/80 hover:bg-teal-600/80",
        showCondition ? "px-2" : "px-1.5",
        stacked && "py-1",
        attachedToJoin && "ml-0 rounded-l-none"
      )}
    >
      {stacked ? (
        <div
          data-testid="run-if-stack"
          className={cn(
            "nowheel max-h-40 w-max max-w-[32rem] overflow-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden",
            "font-mono text-xs leading-4 tracking-tighter"
          )}
        >
          {clauses.map((clause, index) => (
            <div key={index} className="flex gap-1.5">
              <span
                className={cn(
                  "flex h-4 w-4 shrink-0 items-center justify-center font-bold",
                  clause.operator && "opacity-70"
                )}
              >
                {clause.operator ?? (
                  <GitBranch className="size-3" strokeWidth={2.5} />
                )}
              </span>
              <span className="whitespace-pre">{clause.text}</span>
            </div>
          ))}
        </div>
      ) : (
        <span className="flex h-4 items-center space-x-1">
          <GitBranch className="size-3" strokeWidth={2.5} />
          {showCondition && (
            <pre className="text-xs leading-4 tracking-tighter">
              {condition}
            </pre>
          )}
        </span>
      )}
    </Badge>
  )
}
