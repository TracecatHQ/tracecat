// ACTIONS regex - Updated to be more flexible and capture full paths
export const actionsRegexFactory = () =>
  /ACTIONS\.(\w+)\.(result|error)((?:\.[\w\d\[\]]+)*)/g

/**
 * Replaces ACTIONS expressions in a string with their compacted forms while preserving the rest of the string.
 * ACTIONS references are transformed to a more readable format with the @ prefix.
 *
 * Examples:
 * - `ACTIONS.test.result` → `@test`
 * - `ACTIONS.test.result.foo.bar` → `@test..bar` (preserves last field, ignores array indices)
 * - `ACTIONS.test.error` → `@test.error`
 * - `ACTIONS.test.error.foo.bar` → `@test.error..bar`
 * - `ACTIONS.a.result && ACTIONS.b.error` → `@a && @b.error`
 * - `if (ACTIONS.test.result) return ACTIONS.other.error;` → `if (@test) return @other.error;`
 *
 * @param s - String containing ACTIONS expressions to compress
 * @returns string - Original string with ACTIONS references replaced by their compact form with @ prefix
 */
export function compressActionsInString(s: string): string {
  if (typeof s !== "string") {
    throw new TypeError("Input to compressActionsInString must be a string")
  }

  if (!s) return s

  // Replace each match with its compact form
  const regex = actionsRegexFactory()
  return s.replace(regex, (match, actionName, type, path) => {
    // Parse path segments
    const pathSegments = path ? path.split(".").filter(Boolean) : []

    // Generate compact form based on type and path
    const formattedActionName = `@${actionName}`
    if (type === "result") {
      if (pathSegments.length === 0) {
        return formattedActionName
      } else {
        return `${formattedActionName}..${pathSegments[pathSegments.length - 1]}`
      }
    } else if (type === "error") {
      if (pathSegments.length === 0) {
        return `${formattedActionName}.error`
      } else {
        return `${formattedActionName}.error..${pathSegments[pathSegments.length - 1]}`
      }
    }

    // Fallback (should not happen)
    return match
  })
}
// Template expression utilities

/**
 * Creates a regular expression for matching template expressions
 *
 * Template expressions use the syntax ${{ ... }} and can contain:
 * - Action references: ${{ ACTIONS.step_name.result }}
 * - Function calls: ${{ FN.add(1, 2) }}
 * - Input references: ${{ inputs.field_name }}
 * - Secret references: ${{ SECRETS.secret_name.key }}
 * - Mixed content: "Hello ${{ inputs.name }}"
 *
 * @returns Regular expression for matching template expressions
 */
export function createTemplateRegex() {
  // Match template expressions with named capture groups, equivalent to Python's TEMPLATE_STRING
  return /\$\{\{(\s*(.+?)\s*)\}\}/g
}

/**
 * Check if a value contains a template expression pattern
 *
 * Template expressions use the syntax ${{ ... }} and can contain:
 * - Action references: ${{ ACTIONS.step_name.result }}
 * - Function calls: ${{ FN.add(1, 2) }}
 * - Input references: ${{ inputs.field_name }}
 * - Secret references: ${{ SECRETS.secret_name.key }}
 * - Mixed content: "Hello ${{ inputs.name }}"
 *
 * This function is critical for field rendering logic because:
 * - Boolean fields normally render as checkboxes
 * - But if they contain expressions, they must render as text/expression inputs
 * - Same principle applies to other typed fields (numbers, selects, etc.)
 *
 * @param value - The field value to check
 * @returns true if the value contains template expression syntax
 */
export function isExpression(value: unknown): boolean {
  if (typeof value !== "string") {
    return false
  }
  const regex = createTemplateRegex()
  return regex.test(value)
}
