"use client"

import { useEffect } from "react"
import type { ApiError } from "@/client"
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
    console.info("Clearing workspace cookies")
    clearLastWorkspaceId()
  }, [clearLastWorkspaceId])

  return <ErrorPage error={error} />
}
