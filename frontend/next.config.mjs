import { withSentryConfig } from "@sentry/nextjs"

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
