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
const SENSITIVE_QUERY_KEY_PARTS = [...SENSITIVE_KEY_PARTS, "code", "state"]
const REQUEST_URL_KEYS = new Set(["url", "requesturl"])
const REQUEST_QUERY_KEYS = new Set(["querystring"])

/** Removes sensitive values from Sentry payloads before they leave the browser. */
export function beforeSend(
  event: ErrorEvent,
  _hint: EventHint
): ErrorEvent | null {
  return scrubValue(event) as ErrorEvent
}

function scrubValue(value: unknown, depth = 0, key?: string): unknown {
  if (depth > MAX_SCRUB_DEPTH) {
    return REDACTED_VALUE
  }
  if (key && isRequestQueryKey(key)) {
    return scrubQueryValue(value, depth)
  }
  if (Array.isArray(value)) {
    return value.map((item) => scrubValue(item, depth + 1))
  }
  if (typeof value === "string") {
    return scrubRequestString(key, value)
  }
  if (!isRecord(value)) {
    return value
  }

  return Object.fromEntries(
    Object.entries(value).map(([key, nestedValue]) => [
      key,
      shouldRedactKey(key)
        ? REDACTED_VALUE
        : scrubValue(nestedValue, depth + 1, key),
    ])
  )
}

function scrubRequestString(key: string | undefined, value: string): string {
  const normalizedKey = key ? normalizeSensitiveKey(key) : ""
  if (REQUEST_URL_KEYS.has(normalizedKey)) {
    return scrubUrlQuery(value)
  }
  return value
}

function scrubQueryValue(value: unknown, depth: number): unknown {
  if (typeof value === "string") {
    return scrubQueryString(value)
  }
  if (Array.isArray(value)) {
    return value.map((item) => {
      if (isQueryParamPair(item)) {
        const [paramKey, paramValue] = item
        return [
          paramKey,
          shouldRedactQueryKey(String(paramKey))
            ? REDACTED_VALUE
            : scrubValue(paramValue, depth + 1),
        ]
      }
      return scrubValue(item, depth + 1)
    })
  }
  if (isRecord(value)) {
    return Object.fromEntries(
      Object.entries(value).map(([paramKey, paramValue]) => [
        paramKey,
        shouldRedactQueryKey(paramKey)
          ? REDACTED_VALUE
          : scrubValue(paramValue, depth + 1),
      ])
    )
  }
  return value
}

function isQueryParamPair(value: unknown): value is [unknown, unknown] {
  return Array.isArray(value) && value.length === 2
}

function scrubUrlQuery(value: string): string {
  const isAbsoluteUrl = /^[a-z][a-z\d+\-.]*:/i.test(value)
  try {
    const parsedUrl = new URL(value, "https://tracecat.invalid")
    if (!parsedUrl.search) {
      return value
    }
    parsedUrl.search = scrubQueryString(parsedUrl.search)
    if (isAbsoluteUrl) {
      return parsedUrl.toString()
    }
    return `${parsedUrl.pathname}${parsedUrl.search}${parsedUrl.hash}`
  } catch {
    return value
  }
}

function scrubQueryString(value: string): string {
  const queryString = value.startsWith("?") ? value.slice(1) : value
  if (!queryString) {
    return value
  }

  const params = Array.from(new URLSearchParams(queryString).entries())
  if (params.length === 0) {
    return value
  }

  const scrubbedParams = new URLSearchParams()
  for (const [paramKey, paramValue] of params) {
    scrubbedParams.append(
      paramKey,
      shouldRedactQueryKey(paramKey) ? REDACTED_VALUE : paramValue
    )
  }
  return scrubbedParams.toString()
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null
}

function isRequestQueryKey(key: string): boolean {
  return REQUEST_QUERY_KEYS.has(normalizeSensitiveKey(key))
}

function shouldRedactKey(key: string): boolean {
  const normalizedKey = normalizeSensitiveKey(key)
  return SENSITIVE_KEY_PARTS.some((part) =>
    normalizedKey.includes(normalizeSensitiveKey(part))
  )
}

function shouldRedactQueryKey(key: string): boolean {
  const normalizedKey = normalizeSensitiveKey(key)
  return SENSITIVE_QUERY_KEY_PARTS.some((part) =>
    normalizedKey.includes(normalizeSensitiveKey(part))
  )
}

function normalizeSensitiveKey(key: string): string {
  return key.toLowerCase().replace(/[^a-z0-9]/g, "")
}
