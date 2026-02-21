"use client"

import { useRouter } from "next/navigation"
import { useEffect } from "react"
import { CenteredSpinner } from "@/components/loading/spinner"
import { OrgSettingsGitForm } from "@/components/organization/org-settings-git"
import { useEntitlements } from "@/hooks/use-entitlements"

export default function GitSettingsPage() {
  const { hasEntitlement, isLoading } = useEntitlements()
  const router = useRouter()
  const customRegistryEnabled = hasEntitlement("custom_registry")

  useEffect(() => {
    if (!isLoading && !customRegistryEnabled) {
      router.replace("/organization/settings/sso")
    }
  }, [customRegistryEnabled, isLoading, router])

  if (isLoading) return <CenteredSpinner />
  if (!customRegistryEnabled) return null

  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12">
        <div className="flex w-full">
          <div className="items-start space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">
              Git repository
            </h2>
            <p className="text-md text-muted-foreground">
              View and manage your organization Git settings here.
            </p>
          </div>
        </div>

        <OrgSettingsGitForm />
      </div>
    </div>
  )
}
