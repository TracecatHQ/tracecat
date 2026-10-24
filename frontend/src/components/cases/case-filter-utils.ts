import type { CaseFilterField, Condition, Filter } from "@/client"

/** Return immediate children of a boolean group. */
export function childrenOf(node: Filter): Filter[] {
  if ("and" in node) return node.and
  if ("or" in node) return node.or
  if ("not" in node) return [node.not]
  return []
}

/** Count leaves using the same budget as the server. */
export function conditionCount(node: Filter): number {
  if ("field" in node) return 1
  return childrenOf(node).reduce(
    (count, child) => count + conditionCount(child),
    0
  )
}

/** Recognize the explicit comparison-or-unset representation. */
export function includeUnsetCondition(node: Filter): Condition | null {
  if (!("or" in node) || node.or.length !== 2) return null
  const condition = node.or.find(
    (child) => "field" in child && child.op !== "is_null"
  )
  const unset = node.or.find(
    (child) => "field" in child && child.op === "is_null"
  )
  if (
    condition &&
    unset &&
    "field" in condition &&
    "field" in unset &&
    unset.op === "is_null" &&
    condition.field === unset.field
  )
    return condition
  return null
}

/** Plain-language labels for the generated comparison operators. */
export const OP_LABELS: Record<Condition["op"], string> = {
  eq: "equals",
  ne: "does not equal",
  in: "is one of",
  not_in: "is not one of",
  gt: "greater than",
  gte: "at least",
  lt: "less than",
  lte: "at most",
  contains: "contains",
  starts_with: "starts with",
  is_null: "is unset",
}

/** Start a new condition with an existing built-in status. */
export function newCondition(): Condition {
  return { field: "status", op: "eq", value: "new" }
}

/** Count all submitted scalar and list values, including empty strings and duplicates. */
export function filterValueCount(node: Filter): number {
  if ("field" in node) {
    if (Array.isArray(node.value)) return node.value.length
    return node.value == null ? 0 : 1
  }
  return childrenOf(node).reduce(
    (total, child) => total + filterValueCount(child),
    0
  )
}

/** Return the server's eligible values for the selected comparison. */
export function fieldChoices(
  field: CaseFilterField | undefined,
  op: Condition["op"]
) {
  if (["gt", "gte", "lt", "lte"].includes(op))
    return field?.range_choices ?? field?.choices
  return field?.choices
}

/** Keep saved unranked comparisons visible, but require correction before applying. */
export function hasInvalidRangeChoice(
  node: Filter,
  fields: CaseFilterField[]
): boolean {
  if ("field" in node) {
    const field = fields.find((item) => item.field === node.field)
    if (!field?.range_choices || !["gt", "gte", "lt", "lte"].includes(node.op))
      return false
    return (
      typeof node.value !== "string" ||
      !field.range_choices.includes(node.value)
    )
  }
  return childrenOf(node).some((child) => hasInvalidRangeChoice(child, fields))
}
