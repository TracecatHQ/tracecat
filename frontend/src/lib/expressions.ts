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
    const pathSegments = path
      ? path.split(".").filter(Boolean)
      : //   .filter((seg: string) => !/^\d+$/.test(seg) && !/^\[\d+\]$/.test(seg))
        []

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
