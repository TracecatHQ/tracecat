import * as Sentry from "@sentry/nextjs"
import { beforeSend } from "@/lib/sentry"

export type PublicEnvKey = "NEXT_PUBLIC_SENTRY_DSN" | "NEXT_PUBLIC_APP_ENV"

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

export function readPublicEnv(key: PublicEnvKey): string | undefined {
  if (typeof window !== "undefined") {
    return readEnvValue(window.__ENV?.[key]) ?? readBundledPublicEnv(key)
  }
  return readBundledPublicEnv(key)
}

function readBundledPublicEnv(key: PublicEnvKey): string | undefined {
  switch (key) {
    case "NEXT_PUBLIC_SENTRY_DSN":
      return readEnvValue(process.env.NEXT_PUBLIC_SENTRY_DSN)
    case "NEXT_PUBLIC_APP_ENV":
      return readEnvValue(process.env.NEXT_PUBLIC_APP_ENV)
  }
}

function readEnvValue(value: string | undefined): string | undefined {
  return value?.trim() ? value : undefined
}
