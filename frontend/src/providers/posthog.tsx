"use client"

import posthog from "posthog-js"
import { PostHogProvider } from "posthog-js/react"

if (typeof window !== "undefined") {
  const posthogKey = process.env.NEXT_PUBLIC_POSTHOG_KEY
  if (posthogKey) {
    posthog.init(posthogKey, {
      api_host: process.env.NEXT_PUBLIC_POSTHOG_HOST,
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

export function PHProvider({ children }: { children: React.ReactNode }) {
  return <PostHogProvider client={posthog}>{children}</PostHogProvider>
}

export type PHProviderType = typeof PHProvider
