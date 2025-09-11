"use client"

import { useRouter } from "next/navigation"
import { useEffect } from "react"

import { CenteredSpinner } from "@/components/loading/spinner"
import { OrgVCSSettings } from "@/components/organization/org-vcs-settings"
import { useFeatureFlag } from "@/hooks/use-feature-flags"

export default function VCSSettingsPage() {
  const router = useRouter()
  const { isFeatureEnabled, isLoading } = useFeatureFlag()

  useEffect(() => {
    if (!isLoading && !isFeatureEnabled("git-sync")) {
      // Use replace to avoid adding a history entry and prevent back navigation to this page
      router.replace("/not-found")
    }
  }, [isLoading, isFeatureEnabled, router])

  // Show loading while feature flags are being fetched
  if (isLoading) {
    return <CenteredSpinner />
  }

  // Don't render content if feature is disabled (redirect is happening in useEffect)
  if (!isFeatureEnabled("git-sync")) {
    return <CenteredSpinner />
  }

  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12">
        <div className="flex w-full">
          <div className="items-start space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">
              Workflow sync
            </h2>
            <p className="text-md text-muted-foreground">
              Sync workflows to and from your private Git repository.
            </p>
          </div>
        </div>

        <OrgVCSSettings />
      </div>
    </div>
  )
}
