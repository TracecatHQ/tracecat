// ACTIONS regex - Updated to be more flexible and capture full paths
export const actionsRegexFactory = () =>
  /ACTIONS\.(\w+)\.(result|error)((?:\.[\w\d\[\]]+)*)/g

/**
 * Replaces ACTIONS expressions in a string with their compacted forms.
 * Only ACTIONS references are transformed; the rest of the string remains unchanged.
 *
 * Examples:
 * - `ACTIONS.test.result && ACTIONS.other.error` -> `test && other.error`
 * - `ACTIONS.test.result.foo.bar || something else` -> `test..bar || something else`
 *
 * @param s - String containing ACTIONS expressions to compact
 * @returns string - Original string with ACTIONS references replaced by their compact form
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
