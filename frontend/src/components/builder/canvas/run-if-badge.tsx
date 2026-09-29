import { GitBranch } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { compressActionsInString } from "@/lib/expressions"
import { cn } from "@/lib/utils"

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

/**
 * Badge shown above an action node that has a `run_if` condition.
 *
 * In compact mode only the condition icon is shown until the action is
 * hovered or selected, then the badge expands to the full condition.
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
  const showCondition = !compact || expanded

  return (
    <Badge
      aria-label={`Run if ${condition}`}
      data-state={showCondition ? "expanded" : "collapsed"}
      className={cn(
        "border-0 text-xs shadow-none",
        "bg-teal-500/80 hover:bg-teal-600/80",
        showCondition ? "px-2" : "px-1.5",
        attachedToJoin && "ml-0 rounded-l-none"
      )}
    >
      <span className="flex items-center space-x-1">
        <GitBranch className="size-3" strokeWidth={2.5} />
        {showCondition && (
          <pre className="text-xs tracking-tighter">{condition}</pre>
        )}
      </span>
    </Badge>
  )
}
