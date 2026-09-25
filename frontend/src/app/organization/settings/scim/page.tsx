"use client"

import type { ReactNode } from "react"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { EntitlementRequiredEmptyState } from "@/components/entitlement-required-empty-state"
import { CenteredSpinner } from "@/components/loading/spinner"
import {
  OrgSettingsScim,
  ScimPageHeader,
} from "@/components/organization/org-settings-scim"
import { useEntitlements } from "@/hooks/use-entitlements"

function PageShell({ children }: { children: ReactNode }) {
  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12">
        {children}
      </div>
    </div>
  )
}

export default function ScimSettingsPage() {
  const { hasEntitlement, isLoading } = useEntitlements()
  const canManage = useScopeCheck("org:scim:manage")

  if (isLoading || canManage === undefined) {
    return (
      <PageShell>
        <ScimPageHeader />
        <CenteredSpinner />
      </PageShell>
    )
  }
  if (!hasEntitlement("rbac_addons")) {
    return (
      <PageShell>
        <ScimPageHeader />
        <EntitlementRequiredEmptyState
          title="Not available on your plan"
          description="SCIM provisioning requires the access control add-on. Contact your Tracecat account team to enable it."
        />
      </PageShell>
    )
  }
  if (!canManage) {
    return (
      <PageShell>
        <ScimPageHeader />
        <EntitlementRequiredEmptyState
          title="You lack permission"
          description="You need permission to manage SCIM provisioning."
        />
      </PageShell>
    )
  }
  return (
    <PageShell>
      <OrgSettingsScim />
    </PageShell>
  )
}
