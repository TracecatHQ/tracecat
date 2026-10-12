const BASE62 = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
const SHORT_PREFIX = "wf_"
/** The backend pads the base62 part to the longest a UUID can need. */
const SHORT_SUFFIX_LENGTH = 22

/**
 * Convert a workflow UUID to its short ID (`wf_` plus base62), the format
 * the workflow API returns and every workflow link in the app uses.
 *
 * Mirrors `WorkflowUUID.short()` on the backend. Returns `null` for anything
 * that is not a UUID.
 */
export function workflowUuidToShortId(uuid: string): string | null {
  const hex = uuid.replace(/-/g, "")
  if (!/^[0-9a-f]{32}$/i.test(hex)) {
    return null
  }
  const base = BigInt(BASE62.length)
  let value = BigInt(`0x${hex}`)
  let suffix = ""
  while (value > BigInt(0)) {
    suffix = BASE62[Number(value % base)] + suffix
    value = value / base
  }
  return `${SHORT_PREFIX}${suffix.padStart(SHORT_SUFFIX_LENGTH, "0")}`
}

/**
 * Link to the builder of the workflow a form starts.
 *
 * Forms carry the workflow's UUID; the builder route is addressed by the
 * short ID like every other workflow link.
 */
export function formWorkflowHref(
  workspaceId: string,
  workflowUuid: string
): string {
  const workflowId = workflowUuidToShortId(workflowUuid) ?? workflowUuid
  return `/workspaces/${workspaceId}/workflows/${workflowId}`
}

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
