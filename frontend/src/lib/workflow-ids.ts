const BASE62_CHARS =
  "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
const SHORT_ID_LENGTH = 22
const WORKFLOW_ID_PREFIX = "wf_"

/**
 * Convert a workflow UUID to its short `wf_<base62>` ID, the form workflow
 * routes and list responses use. Mirrors `tracecat.identifiers.common.id_to_short`.
 *
 * Long division over the hex digits, not `BigInt`, so it runs in every
 * browser Next.js targets by default.
 *
 * @param uuid A workflow UUID with or without dashes.
 * @returns The short ID, or the input unchanged when it is not a UUID.
 */
export function workflowUuidToShortId(uuid: string): string {
  const hex = uuid.replaceAll("-", "")
  if (!/^[0-9a-f]{32}$/i.test(hex)) {
    return uuid
  }
  let digits = Array.from(hex, (char) => Number.parseInt(char, 16))
  let encoded = ""
  while (digits.length > 0) {
    const quotient: number[] = []
    let remainder = 0
    for (const digit of digits) {
      // At most 61 * 16 + 15, so plain numbers stay exact.
      const value = remainder * 16 + digit
      const q = Math.floor(value / 62)
      remainder = value % 62
      if (quotient.length > 0 || q > 0) {
        quotient.push(q)
      }
    }
    encoded = BASE62_CHARS[remainder] + encoded
    digits = quotient
  }
  return `${WORKFLOW_ID_PREFIX}${encoded.padStart(SHORT_ID_LENGTH, "0")}`
}
