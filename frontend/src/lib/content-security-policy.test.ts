import { buildContentSecurityPolicy } from "@/lib/content-security-policy"

describe("buildContentSecurityPolicy", () => {
  it("includes default Sentry Cloud ingest sources", () => {
    const connectSrc = getConnectSrc(buildContentSecurityPolicy({}))

    expect(connectSrc).toContain("https://*.ingest.sentry.io")
    expect(connectSrc).toContain("https://*.ingest.de.sentry.io")
    expect(connectSrc).toContain("https://*.ingest.us.sentry.io")
  })

  it("adds the runtime public Sentry DSN origin", () => {
    const connectSrc = getConnectSrc(
      buildContentSecurityPolicy({
        NEXT_PUBLIC_SENTRY_DSN: " https://key@sentry.example.com/1 ",
      })
    )

    expect(connectSrc).toContain("https://sentry.example.com")
    expect(connectSrc).not.toContain("key@")
  })

  it("allows PostHog script and connect sources when configured", () => {
    const policy = buildContentSecurityPolicy({
      POSTHOG_KEY: "ph_project",
    })

    expect(getConnectSrc(policy)).toContain("https://*.posthog.com")
    expect(getScriptSrc(policy)).toContain("https://*.posthog.com")
  })

  it("allows the configured public API origin", () => {
    const connectSrc = getConnectSrc(
      buildContentSecurityPolicy({
        NEXT_PUBLIC_API_URL: " https://api.example.com/v1 ",
      })
    )

    expect(connectSrc).toContain("https://api.example.com")
    expect(connectSrc).not.toContain("/v1")
  })

  it("ignores relative public API URLs", () => {
    const connectSrc = getConnectSrc(
      buildContentSecurityPolicy({
        NEXT_PUBLIC_API_URL: "/api",
      })
    )

    expect(connectSrc).toContain("connect-src 'self'")
    expect(connectSrc).not.toContain("/api")
  })

  it("falls back to the server Sentry DSN when the public DSN is blank", () => {
    const connectSrc = getConnectSrc(
      buildContentSecurityPolicy({
        NEXT_PUBLIC_SENTRY_DSN: "",
        SENTRY_DSN: "https://key@self-hosted.example.com/1",
      })
    )

    expect(connectSrc).toContain("https://self-hosted.example.com")
  })

  it("ignores malformed Sentry DSNs", () => {
    const connectSrc = getConnectSrc(
      buildContentSecurityPolicy({
        NEXT_PUBLIC_SENTRY_DSN: "https://[invalid-host/1",
      })
    )

    expect(connectSrc).toContain("https://*.ingest.sentry.io")
    expect(connectSrc).not.toContain("invalid-host")
  })
})

function getConnectSrc(policy: string): string {
  return getDirective(policy, "connect-src")
}

function getScriptSrc(policy: string): string {
  return getDirective(policy, "script-src")
}

function getDirective(policy: string, name: string): string {
  const directive = policy
    .split("; ")
    .find((value) => value.startsWith(`${name} `))

  expect(directive).toBeDefined()
  return directive ?? ""
}
