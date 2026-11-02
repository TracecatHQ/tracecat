import type { Filter } from "@/client"
import {
  $CaseFilterMetadata,
  $Condition,
  $FilterOp,
} from "@/client/schemas.gen"

const limits = $CaseFilterMetadata.properties
const operators: ReadonlySet<string> = new Set($FilterOp.enum)

function isScalar(value: unknown): boolean {
  return (
    typeof value === "string" ||
    typeof value === "boolean" ||
    (typeof value === "number" && Number.isFinite(value))
  )
}

/** Validate persisted structure and server budgets without resolving workspace fields. */
export function restoreCaseFilter(value: unknown): Filter | null {
  let conditions = 0
  let values = 0
  function valid(node: unknown, depth: number): node is Filter {
    if (
      depth > limits.max_depth.default ||
      node === null ||
      typeof node !== "object" ||
      Array.isArray(node)
    )
      return false
    const record = node as Record<string, unknown>
    const keys = Object.keys(record)
    if ("field" in record) {
      if (
        keys.some((key) => !["field", "op", "value"].includes(key)) ||
        typeof record.field !== "string" ||
        record.field.length < $Condition.properties.field.minLength ||
        typeof record.op !== "string" ||
        !operators.has(record.op)
      )
        return false
      conditions += 1
      const input = record.value
      if (input != null) {
        if (Array.isArray(input)) {
          values += input.length
          if (values > limits.max_values.default || !input.every(isScalar))
            return false
        } else {
          values += 1
          if (!isScalar(input)) return false
        }
      }
      return (
        conditions <= limits.max_conditions.default &&
        values <= limits.max_values.default
      )
    }
    if (keys.length !== 1) return false
    if (keys[0] === "not") return valid(record.not, depth + 1)
    if (keys[0] !== "and" && keys[0] !== "or") return false
    const children = record[keys[0]]
    return (
      Array.isArray(children) &&
      children.length > 0 &&
      children.length <= limits.max_conditions.default &&
      children.every((child) => valid(child, depth + 1))
    )
  }
  return valid(value, 1) ? value : null
}
