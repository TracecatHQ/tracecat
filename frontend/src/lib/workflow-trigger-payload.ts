export const DEFAULT_TRIGGER_PAYLOAD = "{}"
const TRIGGER_PAYLOAD_STORAGE_PREFIX = "tracecat:builder:trigger-payload"

interface TriggerPayloadStorageScope {
  userId: string | null
  workspaceId: string
  workflowId: string | null
}

export function triggerPayloadStorageKey({
  userId,
  workspaceId,
  workflowId,
}: TriggerPayloadStorageScope): string {
  return `${TRIGGER_PAYLOAD_STORAGE_PREFIX}:${userId ?? "anonymous"}:${workspaceId}:${workflowId}`
}

export function readPersistedTriggerPayload({
  userId,
  workspaceId,
  workflowId,
}: TriggerPayloadStorageScope): string {
  if (!workflowId || typeof window === "undefined") {
    return DEFAULT_TRIGGER_PAYLOAD
  }

  const storageKey = triggerPayloadStorageKey({
    userId,
    workspaceId,
    workflowId,
  })
  try {
    const stored = window.localStorage.getItem(storageKey)
    if (stored !== null) {
      return stored
    }
  } catch {
    return DEFAULT_TRIGGER_PAYLOAD
  }

  return DEFAULT_TRIGGER_PAYLOAD
}

export function writePersistedTriggerPayload({
  userId,
  workspaceId,
  workflowId,
  triggerPayload,
}: TriggerPayloadStorageScope & {
  triggerPayload: string
}): void {
  if (!workflowId || typeof window === "undefined") {
    return
  }

  try {
    window.localStorage.setItem(
      triggerPayloadStorageKey({ userId, workspaceId, workflowId }),
      triggerPayload
    )
  } catch {
    // Ignore storage failures (e.g. blocked storage or quota exceeded)
  }
}

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
