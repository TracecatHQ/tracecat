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
      NEXT_PUBLIC_POSTHOG_KEY: "ph_project",
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

  it("allows blob fetches for local prompt attachments", () => {
    const connectSrc = getConnectSrc(buildContentSecurityPolicy({}))

    expect(connectSrc).toContain("blob:")
  })

  it("allows externally hosted https images for markdown content", () => {
    const imgSrc = getImgSrc(buildContentSecurityPolicy({}))

    expect(imgSrc).toContain("img-src 'self' data: https:")
    expect(imgSrc).toContain("https:")
  })

  it("allows the configured presigned blob storage origin", () => {
    const policy = buildContentSecurityPolicy({
      NEXT_PUBLIC_BLOB_STORAGE_PRESIGNED_URL_ENDPOINT:
        " https://tracecat-skills.s3.us-east-1.amazonaws.com/uploads ",
    })
    const connectSrc = getConnectSrc(policy)
    const imgSrc = getImgSrc(policy)

    expect(connectSrc).toContain(
      "https://tracecat-skills.s3.us-east-1.amazonaws.com"
    )
    expect(connectSrc).not.toContain("/uploads")
    expect(imgSrc).toContain(
      "https://tracecat-skills.s3.us-east-1.amazonaws.com"
    )
    expect(imgSrc).not.toContain("/uploads")
  })

  it("allows multiple configured presigned blob storage origins", () => {
    const policy = buildContentSecurityPolicy({
      NEXT_PUBLIC_BLOB_STORAGE_PRESIGNED_URL_ENDPOINT:
        " https://tracecat-skills.s3.us-east-1.amazonaws.com/uploads, https://tracecat-attachments.s3.us-east-1.amazonaws.com/files , /s3, not-a-url ",
    })
    const connectSrc = getConnectSrc(policy)
    const imgSrc = getImgSrc(policy)

    expect(connectSrc).toContain(
      "https://tracecat-skills.s3.us-east-1.amazonaws.com"
    )
    expect(connectSrc).toContain(
      "https://tracecat-attachments.s3.us-east-1.amazonaws.com"
    )
    expect(connectSrc).not.toContain("/uploads")
    expect(connectSrc).not.toContain("/files")
    expect(connectSrc).not.toContain("/s3")
    expect(connectSrc).not.toContain("not-a-url")
    expect(imgSrc).toContain(
      "https://tracecat-skills.s3.us-east-1.amazonaws.com"
    )
    expect(imgSrc).toContain(
      "https://tracecat-attachments.s3.us-east-1.amazonaws.com"
    )
  })

  it("ignores relative public API URLs", () => {
    const policy = buildContentSecurityPolicy({
      NEXT_PUBLIC_API_URL: "/api",
      NEXT_PUBLIC_BLOB_STORAGE_PRESIGNED_URL_ENDPOINT: "/s3",
    })
    const connectSrc = getConnectSrc(policy)
    const imgSrc = getImgSrc(policy)

    expect(connectSrc).toContain("connect-src 'self'")
    expect(connectSrc).not.toContain("/api")
    expect(connectSrc).not.toContain("/s3")
    expect(imgSrc).not.toContain("/s3")
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

function getImgSrc(policy: string): string {
  return getDirective(policy, "img-src")
}

function getDirective(policy: string, name: string): string {
  const directive = policy
    .split("; ")
    .find((value) => value.startsWith(`${name} `))

  expect(directive).toBeDefined()
  return directive ?? ""
}
