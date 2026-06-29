export function readEnvValue(value: string | undefined): string | undefined {
  return value?.trim() ? value : undefined
}

export function readSentryDsn(): string | undefined {
  return (
    readEnvValue(process.env.SENTRY_DSN) ??
    readEnvValue(process.env.NEXT_PUBLIC_SENTRY_DSN)
  )
}
