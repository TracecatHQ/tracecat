"use client"

import { AdminTiersTable } from "@/components/admin/admin-tiers-table"
import { CreateTierDialog } from "@/components/admin/create-tier-dialog"
import { CenteredSpinner } from "@/components/loading/spinner"
import { useAdminTiers } from "@/hooks/use-admin"

export default function AdminTiersPage() {
  const { isLoading, error } = useAdminTiers()

  if (isLoading) {
    return <CenteredSpinner />
  }

  if (error) {
    return (
      <div className="text-center text-muted-foreground">
        Failed to load tiers
      </div>
    )
  }

  return (
    <div className="space-y-8">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Tiers</h1>
          <p className="text-muted-foreground">
            Manage subscription tiers and resource limits.
          </p>
        </div>
        <CreateTierDialog />
      </div>
      <AdminTiersTable />
    </div>
  )
}
