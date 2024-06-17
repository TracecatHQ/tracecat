/** @type {import('next').NextConfig} */

const nextConfig = {}

if (process.env.NODE_ENV !== "production") {
  nextConfig.reactStrictMode = false
} else {
  nextConfig.reactStrictMode = true
  nextConfig.output = "standalone"
  generateBuildId: async () => {
    // Return a unique identifier for each build.
    return Date.now().toString()
  }
}

export default nextConfig
