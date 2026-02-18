"use client"

import { useScopeCheck } from "@/components/auth/scope-guard"
import { CenteredSpinner } from "@/components/loading/spinner"
import { RegistryActionsTable } from "@/components/registry/registry-actions-table"

export default function RegistryActionsPage() {
  const canReadRegistry = useScopeCheck("org:registry:read")
  const isLoading = canReadRegistry === undefined

  if (isLoading) return <CenteredSpinner />
  if (!canReadRegistry) return null

  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12">
        <div className="flex w-full">
          <div className="items-start space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">Actions</h2>
            <p className="text-md text-muted-foreground">
              View your organization&apos;s actions here.
            </p>
          </div>
        </div>
        <RegistryActionsTable />
      </div>
    </div>
  )
}
