/** @type {import('next').NextConfig} */

const nextConfig = {
  reactStrictMode: true, // Default to true; overridden in development
  output: "standalone", // Ensure standalone output for production
  generateBuildId: async () => {
    // Return a unique identifier for each build.
    return Date.now().toString();
  },
};

// Override settings for non-production environments
if (process.env.NODE_ENV !== "production") {
  nextConfig.reactStrictMode = false;
}

export default nextConfig;
