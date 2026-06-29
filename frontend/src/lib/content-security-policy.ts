import { readEnvValue } from "@/lib/sentry-env"

type ContentSecurityPolicyEnv = {
  [key: string]: string | undefined
  NEXT_PUBLIC_SENTRY_DSN?: string
  POSTHOG_KEY?: string
  SENTRY_DSN?: string
}

const SENTRY_CONNECT_SOURCES = [
  "https://*.ingest.sentry.io",
  "https://*.ingest.de.sentry.io",
  "https://*.ingest.us.sentry.io",
]

const DIRECTIVES_BEFORE_SCRIPT = [
  "default-src 'self'",
  "worker-src 'self' blob:",
  "frame-ancestors 'none'",
  "img-src 'self' data:",
  "object-src 'none'",
  "base-uri 'self'",
]

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
    ...DIRECTIVES_BEFORE_SCRIPT,
    getScriptSrc(env),
    ...DIRECTIVES_AFTER_SCRIPT,
  ].join("; ")
}

function getConnectSrc(env: ContentSecurityPolicyEnv): string {
  return [
    "connect-src 'self'",
    readEnvValue(env.POSTHOG_KEY) ? "https://*.posthog.com" : undefined,
    ...getSentryConnectSources(readSentryDsn(env)),
  ]
    .filter(Boolean)
    .join(" ")
}

function getScriptSrc(env: ContentSecurityPolicyEnv): string {
  return [
    "script-src 'self' 'unsafe-inline'",
    readEnvValue(env.POSTHOG_KEY) ? "https://*.posthog.com" : undefined,
  ]
    .filter(Boolean)
    .join(" ")
}

function getSentryConnectSources(dsn: string | undefined): string[] {
  const sources = new Set(SENTRY_CONNECT_SOURCES)
  const dsnOrigin = getDsnOrigin(dsn)
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

function getDsnOrigin(dsn: string | undefined): string | undefined {
  if (!dsn) {
    return undefined
  }
  try {
    return new URL(dsn).origin
  } catch {
    return undefined
  }
}
