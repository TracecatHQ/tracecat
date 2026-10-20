import type { CaseFilterField, Filter } from "@/client"
import { Button } from "@/components/ui/button"
import {
  childrenOf,
  includeUnsetCondition,
  OP_LABELS,
} from "./case-filter-utils"

/** Show the applied boolean structure and remove individual conditions in place. */
export function CaseFilterSummary({
  node,
  onChange,
  fields = [],
}: {
  node: Filter
  fields?: CaseFilterField[]
  onChange: (next: Filter | null) => void
}) {
  const unset = includeUnsetCondition(node)
  const condition = "field" in node ? node : unset
  if (condition) {
    const value =
      condition.op === "is_null" ? "" : ` ${JSON.stringify(condition.value)}`
    const fieldLabel =
      fields.find((field) => field.field === condition.field)?.label ??
      condition.field
    const label = `${fieldLabel} ${OP_LABELS[condition.op]}${value}${unset ? " (including unset)" : ""}`
    return (
      <Button
        variant="outline"
        size="sm"
        className="h-auto max-w-full whitespace-normal py-0.5 text-xs"
        aria-label={`Remove ${label}`}
        title="Remove condition"
        onClick={() => onChange(null)}
      >
        {label} ×
      </Button>
    )
  }
  const children = childrenOf(node)
  function replace(index: number, next: Filter | null) {
    const updated = children.flatMap((child, i) => {
      if (i !== index) return [child]
      return next ? [next] : []
    })
    if (updated.length === 0) {
      onChange(null)
      return
    }
    if ("and" in node) onChange({ and: updated })
    else if ("or" in node) onChange({ or: updated })
    else onChange({ not: updated[0] })
  }
  let groupLabel = "All"
  if ("or" in node) groupLabel = "Any"
  if ("not" in node) groupLabel = "Not"
  return (
    <div className="flex flex-wrap items-center gap-1 text-xs text-muted-foreground">
      <span>{groupLabel} (</span>
      {children.map((child, index) => (
        <CaseFilterSummary
          key={index}
          node={child}
          fields={fields}
          onChange={(next) => replace(index, next)}
        />
      ))}
      <span>)</span>
    </div>
  )
}
