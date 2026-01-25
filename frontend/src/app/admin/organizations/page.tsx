"use client"

import { AdminOrganizationsTable } from "@/components/admin/admin-organizations-table"
import { CreateOrganizationDialog } from "@/components/admin/create-organization-dialog"
import { CenteredSpinner } from "@/components/loading/spinner"
import { useAdminOrganizations } from "@/hooks/use-admin"

export default function AdminOrganizationsPage() {
  const { isLoading, error } = useAdminOrganizations()

  if (isLoading) {
    return <CenteredSpinner />
  }

  if (error) {
    return (
      <div className="text-center text-muted-foreground">
        Failed to load organizations
      </div>
    )
  }

  return (
    <div className="space-y-8">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">
            Organizations
          </h1>
          <p className="text-muted-foreground">
            Manage organizations on the platform.
          </p>
        </div>
        <CreateOrganizationDialog />
      </div>
      <AdminOrganizationsTable />
    </div>
  )
}
