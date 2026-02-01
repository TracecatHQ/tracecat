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
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12">
        <div className="flex w-full">
          <div className="items-start space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">Users</h2>
            <p className="text-md text-muted-foreground">
              Manage users and superuser access across the platform.
            </p>
          </div>
        </div>
        <AdminUsersTable />
      </div>
    </div>
  )
}
