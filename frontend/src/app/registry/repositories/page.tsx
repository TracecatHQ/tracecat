"use client"

import { useRouter } from "next/navigation"
import { useEffect } from "react"
import { CenteredSpinner } from "@/components/loading/spinner"
import { RegistryRepositoriesTable } from "@/components/registry/registry-repos-table"
import { useAuth } from "@/hooks/use-auth"
import { useOrgMembership } from "@/hooks/use-org-membership"

export default function RegistryRepositoriesPage() {
  const { userIsLoading } = useAuth()
  const { canAdministerOrg, isLoading: orgMembershipLoading } =
    useOrgMembership()
  const router = useRouter()

  const isLoading = userIsLoading || orgMembershipLoading

  useEffect(() => {
    if (!canAdministerOrg && !isLoading) {
      router.replace("/registry/actions")
    }
  }, [canAdministerOrg, isLoading, router])

  if (isLoading) return <CenteredSpinner />
  if (!canAdministerOrg) return null
  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12">
        <div className="flex w-full">
          <div className="items-start space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">
              Repositories
            </h2>
            <p className="text-base text-muted-foreground">
              View your organization&apos;s action repositories here.
            </p>
          </div>
        </div>
        <RegistryRepositoriesTable />
      </div>
    </div>
  )
}
