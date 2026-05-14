import { withSentryConfig } from "@sentry/nextjs"

const sentryDsn = process.env.NEXT_PUBLIC_SENTRY_DSN ?? process.env.SENTRY_DSN
const sentryConnectSources = getSentryConnectSources(sentryDsn)
const uploadSourcemaps = Boolean(
  process.env.SENTRY_AUTH_TOKEN &&
    process.env.SENTRY_ORG &&
    process.env.SENTRY_PROJECT
)

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true, // Default to true; overridden in development
  output: "standalone", // Ensure standalone output for production
  transpilePackages: ["import-in-the-middle", "require-in-the-middle"],
  experimental: {
    optimizePackageImports: ["lucide-react"],
    serverActions: {
      allowedOrigins: ["login.microsoftonline.com"],
    },
  },
  generateBuildId: async () => {
    // Return a unique identifier for each build.
    return Date.now().toString()
  },
  headers: async () => {
    return [
      {
        // Apply these headers to all routes
        source: "/:path*",
        headers: [
          {
            key: "Strict-Transport-Security",
            value: "max-age=7776000; includeSubDomains",
          },
          {
            key: "X-Content-Type-Options",
            value: "nosniff",
          },
          {
            key: "X-Frame-Options",
            value: "DENY",
          },
          {
            key: "Referrer-Policy",
            value: "strict-origin-when-cross-origin",
          },
          {
            key: "Permissions-Policy",
            value: "document-domain=()",
          },
          {
            key: "Content-Security-Policy",
            value: [
              getConnectSrc(),
              "default-src 'self'",
              "worker-src 'self' blob:",
              "frame-ancestors 'none'",
              "img-src 'self' data:",
              "object-src 'none'",
              "base-uri 'self'",
              getScriptSrc(),
              "script-src-attr 'none'",
              "style-src 'self' 'unsafe-inline'",
            ].join("; "),
          },
        ],
      },
    ]
  },
  redirects: async () => {
    return [
      {
        source: "/workspaces/:workspaceId/agents/presets/:path*",
        destination: "/workspaces/:workspaceId/agents/:path*",
        permanent: true,
      },
    ]
  },
}

// Override settings for non-production environments
if (process.env.NODE_ENV !== "production") {
  nextConfig.reactStrictMode = false
}

function getConnectSrc() {
  return [
    "connect-src 'self'",
    process.env.POSTHOG_KEY ? "https://*.posthog.com" : undefined,
    ...sentryConnectSources,
  ]
    .filter(Boolean)
    .join(" ")
}

function getScriptSrc() {
  return [
    "script-src 'self' 'unsafe-inline'",
    process.env.POSTHOG_KEY ? "https://*.posthog.com" : undefined,
  ]
    .filter(Boolean)
    .join(" ")
}

function getSentryConnectSources(dsn) {
  const sources = new Set([
    "https://*.ingest.sentry.io",
    "https://*.ingest.us.sentry.io",
  ])

  if (!dsn) {
    return []
  }

  try {
    const parsedDsn = new URL(dsn)
    sources.add(parsedDsn.origin)
  } catch {
    return Array.from(sources)
  }

  return Array.from(sources)
}

export default withSentryConfig(nextConfig, {
  org: process.env.SENTRY_ORG,
  project: process.env.SENTRY_PROJECT,
  authToken: process.env.SENTRY_AUTH_TOKEN,
  telemetry: false,
  silent: !process.env.CI,
  sourcemaps: {
    disable: !uploadSourcemaps,
  },
  release: {
    create: uploadSourcemaps,
  },
  treeshake: {
    removeDebugLogging: true,
    removeTracing: true,
  },
  suppressOnRouterTransitionStartWarning: true,
})
