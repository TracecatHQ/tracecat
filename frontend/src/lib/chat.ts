import type { ChatEntity, ModelRequest, ModelResponse } from "@/client"

export type ModelMessage = ModelRequest | ModelResponse

export function isModelMessage(value: unknown): value is ModelMessage {
  return (
    typeof value === "object" &&
    value !== null &&
    "kind" in value &&
    (value.kind === "request" || value.kind === "response") &&
    "parts" in value &&
    Array.isArray(value.parts)
  )
}

export function isChatEntity(value: unknown): value is ChatEntity {
  return value === "case"
}
