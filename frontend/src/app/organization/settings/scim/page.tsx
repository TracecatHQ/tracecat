"use client"

import { EntitlementRequiredEmptyState } from "@/components/entitlement-required-empty-state"
import { CenteredSpinner } from "@/components/loading/spinner"
import { OrgSettingsScimConnection } from "@/components/organization/org-settings-scim-connection"
import { OrgSettingsScimMappings } from "@/components/organization/org-settings-scim-mappings"
import { useEntitlements } from "@/hooks/use-entitlements"
import { useScimConnection } from "@/hooks/use-scim"

function ScimSettings() {
  const { connection, connectionIsLoading, connectionError } =
    useScimConnection()

  if (connectionError?.status === 403) {
    return (
      <EntitlementRequiredEmptyState
        title="You lack permission"
        description="Managing SCIM provisioning requires organization administrator access."
      />
    )
  }

  return (
    <div className="space-y-12">
      <OrgSettingsScimConnection />
      <OrgSettingsScimMappings
        connected={!connectionIsLoading && Boolean(connection)}
      />
    </div>
  )
}

export default function ScimSettingsPage() {
  const { hasEntitlement, isLoading } = useEntitlements()

  let body = <ScimSettings />
  if (isLoading) {
    body = <CenteredSpinner />
  } else if (!hasEntitlement("rbac_addons")) {
    body = (
      <EntitlementRequiredEmptyState
        title="Not available on your plan"
        description="SCIM provisioning requires the access control add-on. Contact your Tracecat account team to enable it."
      />
    )
  }

  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12">
        <div className="flex w-full">
          <div className="items-start space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">SCIM</h2>
            <p className="text-base text-muted-foreground">
              Provision users and groups from your identity provider, and map
              synced groups onto Tracecat groups.
            </p>
          </div>
        </div>
        {body}
      </div>
    </div>
  )
}
