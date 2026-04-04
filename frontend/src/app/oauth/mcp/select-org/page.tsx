"use client"

import Cookies from "js-cookie"
import { useRouter, useSearchParams } from "next/navigation"
import { Suspense, useEffect, useState } from "react"
import { adminListOrganizations } from "@/client/services.gen"
import { CenteredSpinner } from "@/components/loading/spinner"
import { Button } from "@/components/ui/button"
import { useAuth } from "@/hooks/use-auth"

/**
 * MCP auth organization picker for platform superadmins.
 *
 * When a superadmin initiates MCP auth without a selected organization,
 * the internal OIDC issuer redirects here so they can choose one.
 * After selection, the org cookie is set and the authorization flow resumes.
 */
function McpAuthSelectOrgContent() {
  const { user, userIsLoading } = useAuth()
  const router = useRouter()
  const searchParams = useSearchParams()
  const txnId = searchParams?.get("txn")

  const [orgs, setOrgs] = useState<{ id: string; name: string }[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (userIsLoading) return
    if (!user) {
      // Not authenticated — go to sign-in first.
      const returnUrl = `/oauth/mcp/select-org?txn=${encodeURIComponent(txnId ?? "")}`
      router.replace(`/sign-in?returnUrl=${encodeURIComponent(returnUrl)}`)
      return
    }
    if (!txnId) {
      router.replace("/")
      return
    }

    // Fetch orgs
    adminListOrganizations()
      .then((data) => {
        const orgList = (data ?? []).map((org) => ({
          id: org.id,
          name: org.name ?? org.id,
        }))
        setOrgs(orgList)
        setLoading(false)
      })
      .catch((err) => {
        setError(
          err instanceof Error ? err.message : "Failed to load organizations"
        )
        setLoading(false)
      })
  }, [user, userIsLoading, txnId, router])

  function handleSelectOrg(orgId: string) {
    Cookies.set("tracecat-org-id", orgId, { path: "/", sameSite: "lax" })
    // Resume the authorization flow.
    window.location.href = `/api/oauth/mcp/authorize/resume?txn=${encodeURIComponent(txnId ?? "")}`
  }

  if (userIsLoading || loading) {
    return <CenteredSpinner />
  }

  if (error) {
    return (
      <div className="container flex h-full max-w-[600px] flex-col items-center justify-center space-y-4 p-16">
        <h2 className="text-xl font-semibold">Error</h2>
        <p className="text-muted-foreground">{error}</p>
        <Button variant="outline" onClick={() => router.replace("/")}>
          Go home
        </Button>
      </div>
    )
  }

  return (
    <div className="container flex h-full max-w-[600px] flex-col items-center justify-center space-y-6 p-16">
      <div className="space-y-2 text-center">
        <h2 className="text-2xl font-semibold tracking-tight">
          Select organization
        </h2>
        <p className="text-sm text-muted-foreground">
          Choose which organization to use for this MCP session.
        </p>
      </div>
      <div className="flex w-full max-w-[400px] flex-col gap-2">
        {orgs.map((org) => (
          <Button
            key={org.id}
            variant="outline"
            className="w-full justify-start"
            onClick={() => handleSelectOrg(org.id)}
          >
            {org.name}
          </Button>
        ))}
        {orgs.length === 0 && (
          <p className="text-center text-sm text-muted-foreground">
            No organizations found.
          </p>
        )}
      </div>
    </div>
  )
}

export default function McpAuthSelectOrgPage() {
  return (
    <Suspense fallback={<CenteredSpinner />}>
      <McpAuthSelectOrgContent />
    </Suspense>
  )
}
