import { readEnvValue, readSentryDsn } from "@/lib/sentry-env"

describe("readEnvValue", () => {
  it("treats blank values as unset", () => {
    expect(readEnvValue("")).toBeUndefined()
    expect(readEnvValue("   ")).toBeUndefined()
  })

  it("keeps non-blank values", () => {
    expect(readEnvValue("configured")).toBe("configured")
  })
})

describe("readSentryDsn", () => {
  const originalSentryDsn = process.env.SENTRY_DSN
  const originalPublicSentryDsn = process.env.NEXT_PUBLIC_SENTRY_DSN

  afterEach(() => {
    if (originalSentryDsn === undefined) {
      delete process.env.SENTRY_DSN
    } else {
      process.env.SENTRY_DSN = originalSentryDsn
    }
    if (originalPublicSentryDsn === undefined) {
      delete process.env.NEXT_PUBLIC_SENTRY_DSN
    } else {
      process.env.NEXT_PUBLIC_SENTRY_DSN = originalPublicSentryDsn
    }
  })

  it("prefers a non-blank server DSN", () => {
    process.env.SENTRY_DSN = "https://server@example.com/1"
    process.env.NEXT_PUBLIC_SENTRY_DSN = "https://public@example.com/1"

    expect(readSentryDsn()).toBe("https://server@example.com/1")
  })

  it("falls back to public DSN when server DSN is blank", () => {
    process.env.SENTRY_DSN = ""
    process.env.NEXT_PUBLIC_SENTRY_DSN = "https://public@example.com/1"

    expect(readSentryDsn()).toBe("https://public@example.com/1")
  })
})
