/** @type {import('next').NextConfig} */

const nextConfig = {}

if (process.env.NODE_ENV !== "production") {
  nextConfig.reactStrictMode = false
} else {
  nextConfig.reactStrictMode = true
  nextConfig.output = "standalone"
}

export default nextConfig
