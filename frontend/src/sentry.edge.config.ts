import * as Sentry from "@sentry/nextjs"
import { beforeSend } from "@/lib/sentry"
import { readEnvValue, readSentryDsn } from "@/lib/sentry-env"

const sentryDsn = readSentryDsn()

if (sentryDsn) {
  Sentry.init({
    dsn: sentryDsn,
    environment:
      readEnvValue(process.env.NEXT_PUBLIC_APP_ENV) ?? process.env.NODE_ENV,
    sendDefaultPii: false,
    tracesSampleRate: 0,
    beforeSend,
  })
}
