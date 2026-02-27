"use client"

import { useRouter } from "next/navigation"
import { useEffect } from "react"
import { AdminTiersTable } from "@/components/admin/admin-tiers-table"
import { CreateTierDialog } from "@/components/admin/create-tier-dialog"
import { CenteredSpinner } from "@/components/loading/spinner"
import { useAdminTiers } from "@/hooks/use-admin"
import { useAppInfo } from "@/lib/hooks"

export default function AdminTiersPage() {
  const router = useRouter()
  const { appInfo, appInfoIsLoading } = useAppInfo()
  const shouldRedirectToRegistry =
    !appInfoIsLoading && appInfo?.ee_multi_tenant === false
  const shouldLoadTiers = !appInfoIsLoading && !shouldRedirectToRegistry
  const { isLoading, error } = useAdminTiers(true, shouldLoadTiers)

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
        Failed to load tiers
      </div>
    )
  }

  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12">
        <div className="flex w-full">
          <div className="items-start space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">Tiers</h2>
            <p className="text-base text-muted-foreground">
              Manage plans and resource limits.
            </p>
          </div>
          <div className="ml-auto flex items-center space-x-2">
            <CreateTierDialog />
          </div>
        </div>
        <AdminTiersTable />
      </div>
    </div>
  )
}
