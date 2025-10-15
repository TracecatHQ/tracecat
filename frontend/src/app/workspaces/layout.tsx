"use client"

import { ReactFlowProvider } from "@xyflow/react"
import { LogOut } from "lucide-react"
import Image from "next/image"
import { useParams, usePathname } from "next/navigation"
import TracecatIcon from "public/icon.png"
import type React from "react"
import { useEffect, useMemo } from "react"
import { CaseSelectionProvider } from "@/components/cases/case-selection-context"
import { CenteredSpinner } from "@/components/loading/spinner"
import { ControlsHeader } from "@/components/nav/controls-header"
import { DynamicNavbar } from "@/components/nav/dynamic-nav"
import { AppSidebar } from "@/components/sidebar/app-sidebar"
import { Button } from "@/components/ui/button"
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar"
import { useAuthActions } from "@/hooks/use-auth"
import { useWorkspaceManager } from "@/lib/hooks"
import { WorkflowBuilderProvider } from "@/providers/builder"
import { WorkflowProvider } from "@/providers/workflow"
import { WorkspaceIdProvider } from "@/providers/workspace-id"

export default function WorkspaceLayout({
  children,
}: {
  children: React.ReactNode
}) {
  const {
    workspaces,
    workspacesLoading,
    workspacesError,
    setLastWorkspaceId,
    getLastWorkspaceId,
  } = useWorkspaceManager()
  const params = useParams<{ workspaceId?: string; workflowId?: string }>()
  const workspaceId = params?.workspaceId
  const workflowId = params?.workflowId
  const lastViewedWorkspaceId = useMemo(() => {
    if (!workspaces || workspaces.length === 0) {
      return undefined
    }
    const lastViewed = getLastWorkspaceId()
    if (
      lastViewed &&
      lastViewed.trim().length > 0 &&
      workspaces.some((workspace) => workspace.id === lastViewed)
    ) {
      return lastViewed
    }
    return undefined
  }, [getLastWorkspaceId, workspaces])

  useEffect(() => {
    if (workspaceId) {
      setLastWorkspaceId(workspaceId)
    }
  }, [setLastWorkspaceId, workspaceId])
  if (workspacesLoading) {
    return <CenteredSpinner />
  }
  if (workspacesError || !workspaces) {
    throw workspacesError
  }
  let selectedWorkspaceId: string
  if (workspaceId) {
    selectedWorkspaceId = workspaceId
  } else if (lastViewedWorkspaceId) {
    selectedWorkspaceId = lastViewedWorkspaceId
  } else if (workspaces.length > 0) {
    selectedWorkspaceId = workspaces[0].id
  } else {
    return <NoWorkspaces />
  }

  return (
    <WorkspaceIdProvider workspaceId={selectedWorkspaceId}>
      {workflowId ? (
        <WorkflowView workspaceId={selectedWorkspaceId} workflowId={workflowId}>
          <WorkspaceChildren>{children}</WorkspaceChildren>
        </WorkflowView>
      ) : (
        <WorkspaceChildren>{children}</WorkspaceChildren>
      )}
    </WorkspaceIdProvider>
  )
}

function WorkspaceChildren({ children }: { children: React.ReactNode }) {
  const params = useParams<{
    workflowId?: string
    caseId?: string
    runbookId?: string
  }>()
  const pathname = usePathname()
  const isWorkflowBuilder = !!params?.workflowId
  const isCaseDetail = !!params?.caseId
  const isRunbookDetail = !!params?.runbookId
  const isSettingsPage = pathname?.includes("/settings")
  const isOrganizationPage = pathname?.includes("/organization")
  const isRegistryPage = pathname?.includes("/registry")

  // Use old navbar for workflow builder
  if (isWorkflowBuilder) {
    return (
      <div className="no-scrollbar flex h-screen max-h-screen flex-col overflow-hidden">
        <DynamicNavbar />
        <div className="grow overflow-auto">{children}</div>
      </div>
    )
  }

  // Settings, organization and registry pages have their own sidebars
  if (isSettingsPage || isOrganizationPage || isRegistryPage) {
    return <>{children}</>
  }

  // Case detail pages have their own layout with dual SidebarInset
  if (isCaseDetail) {
    return <>{children}</>
  }

  // Runbook detail pages have their own layout with chat sidebar
  if (isRunbookDetail) {
    return <>{children}</>
  }

  // All other workspace pages get the app sidebar
  return (
    <SidebarProvider>
      <AppSidebar />
      <SidebarInset>
        <CaseSelectionProvider>
          <div className="flex h-full flex-1 flex-col">
            <ControlsHeader />
            <div className="flex-1 overflow-y-scroll">{children}</div>
          </div>
        </CaseSelectionProvider>
      </SidebarInset>
    </SidebarProvider>
  )
}

function WorkflowView({
  children,
  workspaceId,
  workflowId,
}: {
  children: React.ReactNode
  workspaceId: string
  workflowId: string
}) {
  return (
    <WorkflowProvider workspaceId={workspaceId} workflowId={workflowId}>
      <ReactFlowProvider>
        <WorkflowBuilderProvider>{children}</WorkflowBuilderProvider>
      </ReactFlowProvider>
    </WorkflowProvider>
  )
}

function NoWorkspaces() {
  const { logout } = useAuthActions()
  const handleLogout = async () => {
    await logout()
  }
  return (
    <main className="container flex size-full max-w-[400px] flex-col items-center justify-center space-y-4">
      <Image src={TracecatIcon} alt="Tracecat" className="mb-4 size-16" />
      <h1 className="text-2xl font-semibold tracking-tight">No workspaces</h1>
      <span className="text-center text-muted-foreground">
        You are not a member of any workspace. Please contact your
        administrator.
      </span>
      <Button variant="outline" onClick={handleLogout}>
        <LogOut className="mr-2 size-4" />
        <span>Logout</span>
      </Button>
    </main>
  )
}
