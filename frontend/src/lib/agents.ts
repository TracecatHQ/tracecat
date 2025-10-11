import type { UIMessage } from "ai"

export function isUIMessageArray(value: unknown): value is UIMessage[] {
  if (!Array.isArray(value)) {
    return false
  }
  return value.every((item) => {
    if (!item || typeof item !== "object") {
      return false
    }
    const candidate = item as {
      id?: unknown
      role?: unknown
      parts?: unknown
    }
    return (
      typeof candidate.id === "string" &&
      typeof candidate.role === "string" &&
      Array.isArray(candidate.parts)
    )
  })
}
