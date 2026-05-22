import type { ErrorEvent, EventHint } from "@sentry/nextjs"

const REDACTED_VALUE = "[Filtered]"
const MAX_SCRUB_DEPTH = 8
const SENSITIVE_KEY_PARTS = [
  "api_key",
  "authorization",
  "cookie",
  "credential",
  "dsn",
  "jwt",
  "keyring",
  "password",
  "private_key",
  "secret",
  "set-cookie",
  "signature",
  "token",
]

/** Removes sensitive values from Sentry payloads before they leave the browser. */
export function beforeSend(
  event: ErrorEvent,
  _hint: EventHint
): ErrorEvent | null {
  return scrubValue(event) as ErrorEvent
}

function scrubValue(value: unknown, depth = 0): unknown {
  if (depth > MAX_SCRUB_DEPTH) {
    return REDACTED_VALUE
  }
  if (Array.isArray(value)) {
    return value.map((item) => scrubValue(item, depth + 1))
  }
  if (!isRecord(value)) {
    return value
  }

  return Object.fromEntries(
    Object.entries(value).map(([key, nestedValue]) => [
      key,
      shouldRedactKey(key)
        ? REDACTED_VALUE
        : scrubValue(nestedValue, depth + 1),
    ])
  )
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null
}

function shouldRedactKey(key: string): boolean {
  const normalizedKey = normalizeSensitiveKey(key)
  return SENSITIVE_KEY_PARTS.some((part) =>
    normalizedKey.includes(normalizeSensitiveKey(part))
  )
}

function normalizeSensitiveKey(key: string): string {
  return key.toLowerCase().replace(/[^a-z0-9]/g, "")
}
