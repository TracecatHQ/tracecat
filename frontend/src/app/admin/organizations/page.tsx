"use client"

import { useRouter } from "next/navigation"
import { useEffect } from "react"
import { AdminOrganizationsTable } from "@/components/admin/admin-organizations-table"
import { CreateOrganizationDialog } from "@/components/admin/create-organization-dialog"
import { CenteredSpinner } from "@/components/loading/spinner"
import { useAdminOrganizations } from "@/hooks/use-admin"
import { useAppInfo } from "@/lib/hooks"

export default function AdminOrganizationsPage() {
  const router = useRouter()
  const { appInfo, appInfoIsLoading } = useAppInfo()
  const shouldRedirectToRegistry =
    !appInfoIsLoading && appInfo?.ee_multi_tenant === false
  const shouldLoadOrganizations = !appInfoIsLoading && !shouldRedirectToRegistry
  const { isLoading, error } = useAdminOrganizations({
    enabled: shouldLoadOrganizations,
  })

  useEffect(() => {
    if (shouldRedirectToRegistry) {
      router.replace("/admin/registry")
    }
  }, [router, shouldRedirectToRegistry])

  if (appInfoIsLoading || isLoading) {
    return <CenteredSpinner />
  }

  if (shouldRedirectToRegistry) {
    return null
  }

  if (error) {
    return (
      <div className="text-center text-muted-foreground">
        Failed to load organizations
      </div>
    )
  }

  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12">
        <div className="flex w-full">
          <div className="items-start space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">
              Organizations
            </h2>
            <p className="text-base text-muted-foreground">
              Manage organizations on the platform.
            </p>
          </div>
          <div className="ml-auto flex items-center space-x-2">
            <CreateOrganizationDialog />
          </div>
        </div>
        <AdminOrganizationsTable />
      </div>
    </div>
  )
}
