"use client"

import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import { Badge } from "@/components/ui/badge"
import { useOrgDomains } from "@/hooks/use-org-domains"

function formatVerificationMethod(method: string): string {
  if (method === "platform_admin") {
    return "Platform-assigned"
  }
  return method.replaceAll("_", " ")
}

export function OrgSettingsDomains() {
  const { domains, isLoading, error } = useOrgDomains()

  if (isLoading) {
    return <CenteredSpinner />
  }
  if (error) {
    return (
      <AlertNotification
        level="error"
        message={`Error loading organization domains: ${error.message}`}
      />
    )
  }

  if (!domains || domains.length === 0) {
    return (
      <div className="rounded-lg border p-4 text-sm text-muted-foreground">
        No domains assigned yet. Contact a platform administrator to add one.
      </div>
    )
  }

  return (
    <div className="space-y-3">
      {domains.map((domain) => (
        <div
          key={domain.id}
          className="flex flex-col gap-3 rounded-lg border p-4 sm:flex-row sm:items-center sm:justify-between"
        >
          <div className="space-y-1">
            <p className="text-sm font-medium">{domain.domain}</p>
            <p className="text-xs text-muted-foreground">
              Verification:{" "}
              {formatVerificationMethod(domain.verification_method)}
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            {domain.is_primary && <Badge variant="secondary">Primary</Badge>}
            <Badge variant={domain.is_active ? "secondary" : "outline"}>
              {domain.is_active ? "Active" : "Inactive"}
            </Badge>
          </div>
        </div>
      ))}
    </div>
  )
}
