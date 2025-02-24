"use client"

import { useEffect } from "react"
import { ApiError } from "@/client"

import { useWorkspaceManager } from "@/lib/hooks"
import ErrorPage from "@/components/error"

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
