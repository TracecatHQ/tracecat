const fs = require("fs")

// Check if VERCEL_ENV is available and set NEXT_PUBLIC_APP_URL accordingly
const appUrl =
  process.env.VERCEL_ENV === "production"
    ? "https://app.tracecat.com"
    : `https://${process.env.VERCEL_URL}`

// Write the environment variable to .env.local
fs.writeFileSync(".env.local", `NEXT_PUBLIC_APP_URL=${appUrl}\n`, {
  encoding: "utf8",
})

console.log(`Set NEXT_PUBLIC_APP_URL to ${appUrl}`)
