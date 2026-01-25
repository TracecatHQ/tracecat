"use client"

import { AdminUsersTable } from "@/components/admin/admin-users-table"
import { CenteredSpinner } from "@/components/loading/spinner"
import { useAdminUsers } from "@/hooks/use-admin"

export default function AdminUsersPage() {
  const { isLoading, error } = useAdminUsers()

  if (isLoading) {
    return <CenteredSpinner />
  }

  if (error) {
    return (
      <div className="text-center text-muted-foreground">
        Failed to load users
      </div>
    )
  }

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Users</h1>
        <p className="text-muted-foreground">
          Manage users and superuser access across the platform.
        </p>
      </div>
      <AdminUsersTable />
    </div>
  )
}
