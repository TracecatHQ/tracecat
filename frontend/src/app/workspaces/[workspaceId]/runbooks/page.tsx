"use client"

import { useRouter } from "next/navigation"
import { Suspense, useEffect } from "react"
import { RunbooksDashboard } from "@/components/dashboard/runbooks-dashboard"
import { CenteredSpinner } from "@/components/loading/spinner"
import { useFeatureFlag } from "@/hooks/use-feature-flags"

export default function RunbooksDashboardPage() {
  const router = useRouter()
  const { isFeatureEnabled, isLoading } = useFeatureFlag()
  const runbooksEnabled = isFeatureEnabled("runbooks")

  useEffect(() => {
    if (!isLoading && !runbooksEnabled) {
      router.replace("/not-found")
    }
  }, [isLoading, runbooksEnabled, router])

  useEffect(() => {
    if (runbooksEnabled) {
      document.title = "Runbooks"
    }
  }, [runbooksEnabled])

  if (isLoading) {
    return <CenteredSpinner />
  }

  if (!runbooksEnabled) {
    return <CenteredSpinner />
  }

  return (
    <Suspense fallback={<CenteredSpinner />}>
      <RunbooksDashboard />
    </Suspense>
  )
}
