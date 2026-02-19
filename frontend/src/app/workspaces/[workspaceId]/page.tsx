"use client"

import Image from "next/image"
import { useRouter } from "next/navigation"
import TracecatIcon from "public/icon.png"
import { useEffect, useMemo } from "react"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { CenteredSpinner } from "@/components/loading/spinner"
import { Button } from "@/components/ui/button"
import { useFeatureFlag } from "@/hooks/use-feature-flags"
import { useWorkspaceId } from "@/providers/workspace-id"

function NoAccessibleSections() {
  const router = useRouter()

  return (
    <main className="container flex size-full max-w-[400px] flex-col items-center justify-center space-y-4">
      <Image src={TracecatIcon} alt="Tracecat" className="mb-4 size-16" />
      <h1 className="text-2xl font-semibold tracking-tight">
        No accessible pages
      </h1>
      <span className="text-center text-muted-foreground">
        You can access this workspace, but you don&apos;t have read permissions
        for any workspace section.
      </span>
      <Button variant="outline" onClick={() => router.replace("/workspaces")}>
        Back to workspaces
      </Button>
    </main>
  )
}

export default function WorkspacePage() {
  const router = useRouter()
  const workspaceId = useWorkspaceId()
  const { isFeatureEnabled, isLoading: featureFlagsLoading } = useFeatureFlag()
  const canViewWorkflows = useScopeCheck("workflow:read")
  const canViewCases = useScopeCheck("case:read")
  const canViewAgents = useScopeCheck("agent:read")
  const canViewTables = useScopeCheck("table:read")
  const canViewVariables = useScopeCheck("variable:read")
  const canViewSecrets = useScopeCheck("secret:read")
  const canViewIntegrations = useScopeCheck("integration:read")
  const canViewMembers = useScopeCheck("workspace:member:read")
  const canViewInbox = useScopeCheck("inbox:read")
  const canExecuteAgents = useScopeCheck("agent:execute")

  const agentPresetsEnabled = isFeatureEnabled("agent-presets")
  const isLoading =
    featureFlagsLoading ||
    [
      canViewWorkflows,
      canViewCases,
      canViewAgents,
      canViewTables,
      canViewVariables,
      canViewSecrets,
      canViewIntegrations,
      canViewMembers,
      canViewInbox,
      canExecuteAgents,
    ].some((value) => value === undefined)

  const landingPath = useMemo(() => {
    if (isLoading) {
      return undefined
    }
    const basePath = `/workspaces/${workspaceId}`
    if (canViewWorkflows === true) {
      return `${basePath}/workflows`
    }
    if (canViewCases === true) {
      return `${basePath}/cases`
    }
    if (agentPresetsEnabled && canViewAgents === true) {
      return `${basePath}/agents`
    }
    if (canViewTables === true) {
      return `${basePath}/tables`
    }
    if (canViewVariables === true) {
      return `${basePath}/variables`
    }
    if (canViewSecrets === true) {
      return `${basePath}/credentials`
    }
    if (canViewIntegrations === true) {
      return `${basePath}/integrations`
    }
    if (canViewMembers === true) {
      return `${basePath}/members`
    }
    if (canViewInbox === true) {
      return `${basePath}/inbox`
    }
    if (canExecuteAgents === true) {
      return `${basePath}/copilot`
    }
    return null
  }, [
    agentPresetsEnabled,
    canExecuteAgents,
    canViewAgents,
    canViewCases,
    canViewInbox,
    canViewIntegrations,
    canViewMembers,
    canViewSecrets,
    canViewTables,
    canViewVariables,
    canViewWorkflows,
    isLoading,
    workspaceId,
  ])

  useEffect(() => {
    if (!landingPath) {
      return
    }
    router.replace(landingPath)
  }, [landingPath, router])

  if (landingPath === undefined || landingPath !== null) {
    return <CenteredSpinner />
  }

  return <NoAccessibleSections />
}
