"use client"

import * as Sentry from "@sentry/nextjs"
import NextError from "next/error"
import { PublicEnvScript } from "next-runtime-env"
import { useEffect } from "react"
import { initBrowserSentry } from "@/lib/sentry-client"

interface GlobalErrorProps {
  error: Error & { digest?: string }
}

export default function GlobalError({ error }: GlobalErrorProps) {
  useEffect(() => {
    initBrowserSentry()
    Sentry.captureException(error)
  }, [error])

  return (
    <html lang="en">
      <head>
        <PublicEnvScript />
      </head>
      <body>
        <NextError statusCode={0} />
      </body>
    </html>
  )
}
