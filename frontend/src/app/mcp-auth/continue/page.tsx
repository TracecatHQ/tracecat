"use client"

import { useRouter, useSearchParams } from "next/navigation"
import { Suspense, useEffect } from "react"

import { CenteredSpinner } from "@/components/loading/spinner"
import { useAuth } from "@/hooks/use-auth"

/**
 * MCP auth resume page.
 *
 * After the internal OIDC issuer redirects here because the user has no
 * active session, this page either:
 * - Redirects to sign-in (preserving the txn param via returnUrl), or
 * - Redirects back to the OIDC authorize/resume endpoint if already logged in.
 */
function McpAuthContinueContent() {
  const { user, userIsLoading } = useAuth()
  const router = useRouter()
  const searchParams = useSearchParams()
  const txnId = searchParams?.get("txn")

  useEffect(() => {
    if (userIsLoading) return
    if (!txnId) {
      router.replace("/")
      return
    }

    if (user) {
      // Already logged in — resume the authorization flow.
      window.location.href = `/api/mcp-oidc/authorize/resume?txn=${encodeURIComponent(txnId)}`
      return
    }

    // Not logged in — redirect to sign-in with a return URL back here.
    const returnUrl = `/mcp-auth/continue?txn=${encodeURIComponent(txnId)}`
    router.replace(`/sign-in?returnUrl=${encodeURIComponent(returnUrl)}`)
  }, [user, userIsLoading, txnId, router])

  return <CenteredSpinner />
}

export default function McpAuthContinuePage() {
  return (
    <Suspense fallback={<CenteredSpinner />}>
      <McpAuthContinueContent />
    </Suspense>
  )
}
