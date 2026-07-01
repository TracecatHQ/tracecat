"use client"

import * as Sentry from "@sentry/nextjs"
import { useEffect } from "react"
import { ApiError } from "@/client"
import ErrorPage from "@/components/error"
import { useWorkspaceManager } from "@/lib/hooks"

export default function Error({
  error,
}: {
  error: ApiError | (Error & { digest?: string })
}) {
  const { clearLastWorkspaceId } = useWorkspaceManager()
  // When error is rendered, clear workspace cookies
  // to prevent reviving the workspace on a new page load
  useEffect(() => {
    if (shouldCaptureRouteError(error)) {
      Sentry.captureException(error)
    }
    console.info("Clearing workspace cookies")
    clearLastWorkspaceId()
  }, [clearLastWorkspaceId, error])

  return <ErrorPage error={error} />
}

function shouldCaptureRouteError(
  error: ApiError | (Error & { digest?: string })
): boolean {
  return !(error instanceof ApiError) || error.status >= 500
}
