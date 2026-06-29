import { readPublicEnv } from "@/lib/sentry-client"

jest.mock("@sentry/nextjs", () => ({
  getClient: jest.fn(),
  init: jest.fn(),
}))

describe("readPublicEnv", () => {
  const originalSentryDsn = process.env.NEXT_PUBLIC_SENTRY_DSN
  const originalAppEnv = process.env.NEXT_PUBLIC_APP_ENV

  afterEach(() => {
    if (originalSentryDsn === undefined) {
      delete process.env.NEXT_PUBLIC_SENTRY_DSN
    } else {
      process.env.NEXT_PUBLIC_SENTRY_DSN = originalSentryDsn
    }
    if (originalAppEnv === undefined) {
      delete process.env.NEXT_PUBLIC_APP_ENV
    } else {
      process.env.NEXT_PUBLIC_APP_ENV = originalAppEnv
    }
    Reflect.deleteProperty(window, "__ENV")
  })

  it("prefers runtime public env values", () => {
    process.env.NEXT_PUBLIC_SENTRY_DSN = "https://bundled@example.com/1"
    window.__ENV = {
      NEXT_PUBLIC_SENTRY_DSN: "https://runtime@example.com/1",
    } as unknown as NodeJS.ProcessEnv

    expect(readPublicEnv("NEXT_PUBLIC_SENTRY_DSN")).toBe(
      "https://runtime@example.com/1"
    )
  })

  it("falls back to statically accessed bundled public env values", () => {
    process.env.NEXT_PUBLIC_APP_ENV = "test-env"
    window.__ENV = {} as NodeJS.ProcessEnv

    expect(readPublicEnv("NEXT_PUBLIC_APP_ENV")).toBe("test-env")
  })

  it("falls back to bundled public env values for blank runtime values", () => {
    process.env.NEXT_PUBLIC_SENTRY_DSN = "https://bundled@example.com/1"
    window.__ENV = {
      NEXT_PUBLIC_SENTRY_DSN: "",
    } as unknown as NodeJS.ProcessEnv

    expect(readPublicEnv("NEXT_PUBLIC_SENTRY_DSN")).toBe(
      "https://bundled@example.com/1"
    )
  })
})
