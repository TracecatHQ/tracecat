"use client"

import { AlertCircle } from "lucide-react"
import Image from "next/image"
import { useRouter, useSearchParams } from "next/navigation"
import TracecatIcon from "public/icon.png"
import { Suspense, useEffect, useState } from "react"
import { CenteredSpinner } from "@/components/loading/spinner"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Checkbox } from "@/components/ui/checkbox"
import { useAuth } from "@/hooks/use-auth"
import { client } from "@/lib/api"
import { useMutation, useQuery } from "@/lib/query"

interface OrganizationOption {
  id: string
  name: string
  slug: string
}

interface OrganizationSelectionDetails {
  organizations: OrganizationOption[]
  active_organization_id: string
}

interface OrganizationSelectionResult {
  redirect_url: string
}

const SELECTION_PATH = "/oauth/mcp/authorize/organizations"

function getErrorDescription(error: unknown): string {
  if (
    typeof error === "object" &&
    error !== null &&
    "response" in error &&
    typeof error.response === "object" &&
    error.response !== null &&
    "data" in error.response
  ) {
    const data = error.response.data as { error_description?: unknown }
    if (typeof data?.error_description === "string") {
      return data.error_description
    }
  }
  return "Something went wrong. Reconnect from your MCP client."
}

/**
 * MCP organization selection page.
 *
 * Multi-org users land here from the internal OIDC issuer to choose which
 * organizations one MCP connection can access.
 */
function McpOrganizationsContent() {
  const { user, userIsLoading } = useAuth()
  const router = useRouter()
  const searchParams = useSearchParams()
  const txnId = searchParams?.get("txn") ?? null
  const [selected, setSelected] = useState<Set<string> | null>(null)

  useEffect(() => {
    if (userIsLoading || user) return
    const returnUrl = txnId
      ? `/oauth/mcp/organizations?txn=${encodeURIComponent(txnId)}`
      : "/"
    router.replace(`/sign-in?returnUrl=${encodeURIComponent(returnUrl)}`)
  }, [user, userIsLoading, txnId, router])

  const {
    data: details,
    isLoading: detailsAreLoading,
    error: detailsError,
  } = useQuery<OrganizationSelectionDetails>({
    queryKey: ["mcp-oauth-organizations", txnId],
    queryFn: async () => {
      const response = await client.get<OrganizationSelectionDetails>(
        SELECTION_PATH,
        { params: { txn: txnId } }
      )
      return response.data
    },
    enabled: !!txnId && !!user,
    retry: false,
  })

  useEffect(() => {
    if (details && selected === null) {
      setSelected(new Set(details.organizations.map((org) => org.id)))
    }
  }, [details, selected])

  const submitMutation = useMutation({
    mutationFn: async (decision: "allow" | "deny") => {
      const response = await client.post<OrganizationSelectionResult>(
        SELECTION_PATH,
        {
          txn: txnId,
          organization_ids: Array.from(selected ?? []),
          decision,
        }
      )
      return response.data
    },
    onSuccess: ({ redirect_url }) => {
      window.location.href = redirect_url
    },
  })

  if (!txnId || detailsError) {
    return (
      <Card className="w-full max-w-md">
        <CardHeader className="items-center text-center">
          <AlertCircle className="mb-4 size-12 text-destructive" />
          <CardTitle>Can&apos;t continue sign-in</CardTitle>
          <CardDescription>
            {txnId
              ? getErrorDescription(detailsError)
              : "This sign-in link is missing its request. Reconnect from your MCP client."}
          </CardDescription>
        </CardHeader>
      </Card>
    )
  }

  if (userIsLoading || !user || detailsAreLoading || !details || !selected) {
    return <CenteredSpinner />
  }

  const toggle = (orgId: string, checked: boolean) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (checked) {
        next.add(orgId)
      } else {
        next.delete(orgId)
      }
      return next
    })
  }

  const isSubmitting = submitMutation.isPending || submitMutation.isSuccess

  return (
    <Card className="w-full max-w-md">
      <CardHeader className="items-center text-center">
        <Image src={TracecatIcon} alt="Tracecat" className="mb-4 size-16" />
        <CardTitle>Choose organizations</CardTitle>
        <CardDescription>
          Select the organizations this MCP connection can access. The agent
          acts with your permissions in each one.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="divide-y rounded-lg border">
          {details.organizations.map((org) => {
            const inputId = `mcp-org-${org.id}`
            return (
              <label
                key={org.id}
                htmlFor={inputId}
                className="flex cursor-pointer items-center gap-3 p-3 text-sm hover:bg-muted/50"
              >
                <Checkbox
                  id={inputId}
                  checked={selected.has(org.id)}
                  onCheckedChange={(checked) =>
                    toggle(org.id, checked === true)
                  }
                  disabled={isSubmitting}
                />
                <div className="flex min-w-0 flex-1 flex-col">
                  <span className="truncate font-medium">{org.name}</span>
                  <span className="truncate text-xs text-muted-foreground">
                    {org.slug}
                  </span>
                </div>
                {org.id === details.active_organization_id && (
                  <span className="text-xs text-muted-foreground">Current</span>
                )}
              </label>
            )
          })}
        </div>
        {submitMutation.isError && (
          <p className="text-sm text-destructive">
            {getErrorDescription(submitMutation.error)}
          </p>
        )}
      </CardContent>
      <CardFooter className="flex-col gap-3">
        <Button
          className="w-full"
          onClick={() => submitMutation.mutate("allow")}
          disabled={isSubmitting || selected.size === 0}
        >
          {selected.size === 0
            ? "Select at least one organization"
            : `Allow access to ${selected.size} ${
                selected.size === 1 ? "organization" : "organizations"
              }`}
        </Button>
        <Button
          variant="ghost"
          className="w-full"
          onClick={() => submitMutation.mutate("deny")}
          disabled={isSubmitting}
        >
          Cancel
        </Button>
      </CardFooter>
    </Card>
  )
}

export default function McpOrganizationsPage() {
  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <Suspense fallback={<CenteredSpinner />}>
        <McpOrganizationsContent />
      </Suspense>
    </div>
  )
}
