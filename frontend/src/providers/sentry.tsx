"use client"

import type { ReactNode } from "react"
import { useEffect } from "react"
import { initBrowserSentry } from "@/lib/sentry-client"

export function SentryProvider({ children }: { children: ReactNode }) {
  initBrowserSentry()

  useEffect(() => {
    initBrowserSentry()
  }, [])

  return children
}
