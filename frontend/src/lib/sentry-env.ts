export function readEnvValue(value: string | undefined): string | undefined {
  const trimmedValue = value?.trim()
  return trimmedValue || undefined
}

export function readSentryDsn(): string | undefined {
  return (
    readEnvValue(process.env.SENTRY_DSN) ??
    readEnvValue(process.env.NEXT_PUBLIC_SENTRY_DSN)
  )
}
