import { readEnvValue } from "@/lib/sentry-env"

type ContentSecurityPolicyEnv = {
  [key: string]: string | undefined
  NEXT_PUBLIC_API_URL?: string
  NEXT_PUBLIC_BLOB_STORAGE_PRESIGNED_URL_ENDPOINT?: string
  NEXT_PUBLIC_POSTHOG_KEY?: string
  NEXT_PUBLIC_SENTRY_DSN?: string
  SENTRY_DSN?: string
}

const SENTRY_CONNECT_SOURCES = [
  "https://*.ingest.sentry.io",
  "https://*.ingest.de.sentry.io",
  "https://*.ingest.us.sentry.io",
]

const DIRECTIVES_BEFORE_IMAGE = [
  "default-src 'self'",
  "worker-src 'self' blob:",
  "frame-ancestors 'none'",
]

const DIRECTIVES_AFTER_IMAGE = ["object-src 'none'", "base-uri 'self'"]

const DIRECTIVES_AFTER_SCRIPT = [
  "script-src-attr 'none'",
  "style-src 'self' 'unsafe-inline'",
]

/** Builds the frontend CSP from runtime environment values. */
export function buildContentSecurityPolicy(
  env: ContentSecurityPolicyEnv = process.env
): string {
  return [
    getConnectSrc(env),
    ...DIRECTIVES_BEFORE_IMAGE,
    getImgSrc(env),
    ...DIRECTIVES_AFTER_IMAGE,
    getScriptSrc(env),
    ...DIRECTIVES_AFTER_SCRIPT,
  ].join("; ")
}

function getConnectSrc(env: ContentSecurityPolicyEnv): string {
  return [
    "connect-src 'self'",
    "blob:",
    getUrlOrigin(env.NEXT_PUBLIC_API_URL),
    ...getUrlOrigins(env.NEXT_PUBLIC_BLOB_STORAGE_PRESIGNED_URL_ENDPOINT),
    readEnvValue(env.NEXT_PUBLIC_POSTHOG_KEY)
      ? "https://*.posthog.com"
      : undefined,
    ...getSentryConnectSources(readSentryDsn(env)),
  ]
    .filter(Boolean)
    .join(" ")
}

function getImgSrc(env: ContentSecurityPolicyEnv): string {
  return [
    "img-src 'self' data:",
    ...getUrlOrigins(env.NEXT_PUBLIC_BLOB_STORAGE_PRESIGNED_URL_ENDPOINT),
  ]
    .filter(Boolean)
    .join(" ")
}

function getScriptSrc(env: ContentSecurityPolicyEnv): string {
  return [
    "script-src 'self' 'unsafe-inline'",
    readEnvValue(env.NEXT_PUBLIC_POSTHOG_KEY)
      ? "https://*.posthog.com"
      : undefined,
  ]
    .filter(Boolean)
    .join(" ")
}

function getSentryConnectSources(dsn: string | undefined): string[] {
  const sources = new Set(SENTRY_CONNECT_SOURCES)
  const dsnOrigin = getUrlOrigin(dsn)
  if (dsnOrigin) {
    sources.add(dsnOrigin)
  }
  return Array.from(sources)
}

function readSentryDsn(env: ContentSecurityPolicyEnv): string | undefined {
  return (
    readEnvValue(env.NEXT_PUBLIC_SENTRY_DSN) ?? readEnvValue(env.SENTRY_DSN)
  )
}

function getUrlOrigins(value: string | undefined): string[] {
  const urls = readEnvValue(value)
  if (!urls) {
    return []
  }

  const origins = new Set<string>()
  for (const url of urls.split(",")) {
    const origin = getUrlOrigin(url)
    if (origin) {
      origins.add(origin)
    }
  }
  return Array.from(origins)
}

function getUrlOrigin(value: string | undefined): string | undefined {
  const url = readEnvValue(value)
  if (!url) {
    return undefined
  }
  try {
    return new URL(url).origin
  } catch {
    return undefined
  }
}
