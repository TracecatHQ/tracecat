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
  const org = searchParams?.get("org")

  useEffect(() => {
    if (userIsLoading) return
    if (!txnId) {
      router.replace("/")
      return
    }

    const orgQuery = org ? `&org=${encodeURIComponent(org)}` : ""

    if (user) {
      // Already logged in — resume the authorization flow.
      window.location.href = `/api/oauth/mcp/authorize/resume?txn=${encodeURIComponent(txnId)}${orgQuery}`
      return
    }

    // Not logged in — redirect to sign-in with a return URL back here.
    const returnUrl = `/oauth/mcp/continue?txn=${encodeURIComponent(txnId)}${orgQuery}`
    const signInOrgQuery = org ? `&org=${encodeURIComponent(org)}` : ""
    router.replace(
      `/sign-in?returnUrl=${encodeURIComponent(returnUrl)}${signInOrgQuery}`
    )
  }, [user, userIsLoading, txnId, org, router])

  return <CenteredSpinner />
}

export default function McpAuthContinuePage() {
  return (
    <Suspense fallback={<CenteredSpinner />}>
      <McpAuthContinueContent />
    </Suspense>
  )
}
