import type { Condition, Filter } from "@/client"

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
