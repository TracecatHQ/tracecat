export const DEFAULT_TRIGGER_PAYLOAD = "{}"

export function validateTriggerPayload(payload: string): string | null {
  const normalized = payload.trim()
  if (!normalized) {
    return null
  }
  try {
    JSON.parse(normalized)
    return null
  } catch (error) {
    if (error instanceof Error) {
      return `Invalid JSON format: ${error.message}`
    }
    return "Invalid JSON format: Unknown error occurred"
  }
}

export function parseTriggerPayload(payload: string): unknown {
  const normalized = payload.trim()
  if (!normalized) {
    return undefined
  }
  return JSON.parse(normalized)
}
