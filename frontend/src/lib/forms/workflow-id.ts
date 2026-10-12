const BASE62 = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
const SHORT_PREFIX = "wf_"

/**
 * Convert a short workflow ID (`wf_` plus base62) to its UUID.
 *
 * The workflow list returns short IDs while forms carry the workflow's UUID,
 * so matching a form to its workflow needs one of them converted. Returns
 * `null` for anything that is not a short workflow ID.
 */
export function workflowShortIdToUuid(shortId: string): string | null {
  if (!shortId.startsWith(SHORT_PREFIX)) {
    return null
  }
  const base = BigInt(BASE62.length)
  let value = BigInt(0)
  for (const char of shortId.slice(SHORT_PREFIX.length)) {
    const digit = BASE62.indexOf(char)
    if (digit === -1) {
      return null
    }
    value = value * base + BigInt(digit)
  }
  const hex = value.toString(16).padStart(32, "0")
  if (hex.length !== 32) {
    return null
  }
  return [
    hex.slice(0, 8),
    hex.slice(8, 12),
    hex.slice(12, 16),
    hex.slice(16, 20),
    hex.slice(20),
  ].join("-")
}
