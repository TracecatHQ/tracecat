/** @type {import('next').NextConfig} */

const nextConfig = {
  reactStrictMode: true, // Default to true; overridden in development
  output: "standalone", // Ensure standalone output for production
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
    // Content-Security-Policy is set at runtime in src/middleware.ts (see
    // src/lib/csp.ts) so a deployment can extend it without a rebuild. The
    // middleware matcher skips _next/static, _next/image, favicon.ico and
    // image files, so those responses carry the headers below but no CSP.
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

export default nextConfig
