"use client"

import posthog from "posthog-js"
import { PostHogProvider } from "posthog-js/react"

export const initPostHog = () => {
  // @ts-ignore
  console.log("Initializing PostHog 👋")
  try {
    if (typeof window !== "undefined") {
      // @ts-ignore
      const posthogKey = process.env.NEXT_PUBLIC_POSTHOG_KEY
      if (
        process.env.NODE_ENV === "production" &&
        process.env.ENABLE_TELEMETRY === "true" &&
        posthogKey
      ) {
        posthog.init(posthogKey, {
          api_host: process.env.NEXT_PUBLIC_POSTHOG_INGEST_HOST,
          ui_host: process.env.NEXT_PUBLIC_POSTHOG_HOST,
          persistence: "memory", // We don't use cookies for analytics!
          capture_pageview: false,
          // Disable session recording by default
          disable_session_recording:
            process.env.NEXT_PUBLIC_DISABLE_SESSION_RECORDING === "true",
          session_recording: {
            // If even session recording is enabled,
            // we mask all inputs and text for maximum privacy
            maskAllInputs: true,
            maskTextSelector: "*",
          },
        })
      }
    }
    return posthog
  } catch (e) {
    console.log("posthog err", e)
  }
  return undefined
}

export function PHProvider({ children }: { children: React.ReactNode }) {
  const ph = initPostHog()
  return <PostHogProvider client={ph}>{children}</PostHogProvider>
}
