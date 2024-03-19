// app/PostHogPageView.jsx
"use client"

import { useEffect } from "react"
import { usePathname, useSearchParams } from "next/navigation"
import { usePostHog } from "posthog-js/react"

export default function PostHogPageView() {
  const pathname = usePathname()
  const searchParams = useSearchParams()
  const posthog = usePostHog()
  // Track pageviews
  useEffect(() => {
    if (pathname && posthog) {
      let url = window.origin + pathname
      if (searchParams.toString()) {
        url = url + `?${searchParams.toString()}`
      }
      posthog.capture("$pageview", {
        $current_url: url,
      })
    }
  }, [pathname, searchParams, posthog])

  return null
}
