/** @type {import('next').NextConfig} */

// Optional path prefix for serving the app behind a reverse proxy at a sub-path
// (e.g. NEXT_PUBLIC_BASE_PATH=/tracecat to serve under https://example.com/tracecat).
// Must be set at build time — Next.js inlines this value into client bundles.
// Empty string (default) means the app is served at the domain root.
const basePath = process.env.NEXT_PUBLIC_BASE_PATH || ""

const nextConfig = {
  reactStrictMode: true, // Default to true; overridden in development
  output: "standalone", // Ensure standalone output for production
  basePath,
  assetPrefix: basePath || undefined,
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
            value: process.env.POSTHOG_KEY
              ? [
                  "connect-src 'self' https://*.posthog.com",
                  "default-src 'self'",
                  "worker-src 'self' blob:",
                  "frame-ancestors 'none'",
                  "img-src 'self' data:",
                  "object-src 'none'",
                  "base-uri 'self'",
                  "script-src 'self' 'unsafe-inline' https://*.posthog.com",
                  "script-src-attr 'none'",
                  "style-src 'self' 'unsafe-inline'",
                ].join("; ")
              : [
                  "connect-src 'self'",
                  "default-src 'self'",
                  "worker-src 'self' blob:",
                  "frame-ancestors 'none'",
                  "img-src 'self' data:",
                  "object-src 'none'",
                  "base-uri 'self'",
                  "script-src 'self' 'unsafe-inline'",
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

export default nextConfig
