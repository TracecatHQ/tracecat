const BASE62_CHARS =
  "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
const SHORT_ID_LENGTH = 22
const WORKFLOW_ID_PREFIX = "wf_"
// BigInt() calls, not `62n` literals: the build targets ES2018.
const BIG_ZERO = BigInt(0)
const BIG_BASE = BigInt(62)

/**
 * Convert a workflow UUID to its short `wf_<base62>` ID, the form workflow
 * routes and list responses use. Mirrors `tracecat.identifiers.common.id_to_short`.
 *
 * @param uuid A workflow UUID with or without dashes.
 * @returns The short ID, or the input unchanged when it is not a UUID.
 */
export function workflowUuidToShortId(uuid: string): string {
  const hex = uuid.replaceAll("-", "")
  if (!/^[0-9a-f]{32}$/i.test(hex)) {
    return uuid
  }
  let num = BigInt(`0x${hex}`)
  let encoded = ""
  while (num > BIG_ZERO) {
    encoded = BASE62_CHARS[Number(num % BIG_BASE)] + encoded
    num /= BIG_BASE
  }
  return `${WORKFLOW_ID_PREFIX}${encoded.padStart(SHORT_ID_LENGTH, "0")}`
}
