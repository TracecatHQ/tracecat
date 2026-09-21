"use client"

import { useScopeCheck } from "@/components/auth/scope-guard"
import { EntitlementRequiredEmptyState } from "@/components/entitlement-required-empty-state"
import { CenteredSpinner } from "@/components/loading/spinner"
import { OrgSettingsScimConnection } from "@/components/organization/org-settings-scim-connection"
import { OrgSettingsScimMappings } from "@/components/organization/org-settings-scim-mappings"
import { useEntitlements } from "@/hooks/use-entitlements"
import { useScimConnection } from "@/hooks/use-scim"

function ScimSettings() {
  const { connection, connectionIsLoading, connectionError } =
    useScimConnection()

  if (connectionIsLoading) {
    return <CenteredSpinner />
  }

  if (connectionError?.status === 403) {
    return (
      <EntitlementRequiredEmptyState
        title="You lack permission"
        description="You need permission to manage SCIM provisioning."
      />
    )
  }

  return (
    <div className="space-y-12">
      <OrgSettingsScimConnection />
      {!connectionError && (
        <OrgSettingsScimMappings
          connected={!connectionIsLoading && Boolean(connection)}
          status={connection?.status}
          revoked={Boolean(connection?.revoked_at)}
        />
      )}
    </div>
  )
}

export default function ScimSettingsPage() {
  const { hasEntitlement, isLoading } = useEntitlements()
  const canManage = useScopeCheck("org:scim:manage")

  let body = <ScimSettings />
  if (isLoading || canManage === undefined) {
    body = <CenteredSpinner />
  } else if (!hasEntitlement("rbac_addons")) {
    body = (
      <EntitlementRequiredEmptyState
        title="Not available on your plan"
        description="SCIM provisioning requires the access control add-on. Contact your Tracecat account team to enable it."
      />
    )
  } else if (!canManage) {
    body = (
      <EntitlementRequiredEmptyState
        title="You lack permission"
        description="You need permission to manage SCIM provisioning."
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
