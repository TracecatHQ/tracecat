import * as Sentry from "@sentry/nextjs"
import { beforeSend } from "@/lib/sentry"

type PublicEnvKey = `NEXT_PUBLIC_${string}`

export function initBrowserSentry(): boolean {
  if (Sentry.getClient()) {
    return true
  }

  const sentryDsn = readPublicEnv("NEXT_PUBLIC_SENTRY_DSN")
  if (!sentryDsn) {
    return false
  }

  Sentry.init({
    dsn: sentryDsn,
    environment: readPublicEnv("NEXT_PUBLIC_APP_ENV") ?? process.env.NODE_ENV,
    sendDefaultPii: false,
    tracesSampleRate: 0,
    beforeSend,
  })

  return true
}

function readPublicEnv(key: PublicEnvKey): string | undefined {
  if (typeof window !== "undefined") {
    return window.__ENV?.[key] ?? process.env[key]
  }
  return process.env[key]
}
