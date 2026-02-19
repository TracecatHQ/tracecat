"use client"

import { ReactFlowProvider } from "@xyflow/react"
import { LogOut, Plus, Shield } from "lucide-react"
import Image from "next/image"
import Link from "next/link"
import { useParams, usePathname, useRouter } from "next/navigation"
import TracecatIcon from "public/icon.png"
import type React from "react"
import { useEffect, useMemo, useState } from "react"
import { ApiError } from "@/client"
import { NoOrganizationAccess } from "@/components/auth/no-organization-access"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { CaseSelectionProvider } from "@/components/cases/case-selection-context"
import { CenteredSpinner } from "@/components/loading/spinner"
import { ControlsHeader } from "@/components/nav/controls-header"
import { DynamicNavbar } from "@/components/nav/dynamic-nav"
import { AppSidebar } from "@/components/sidebar/app-sidebar"
import { Button } from "@/components/ui/button"
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar"
import { useAuth, useAuthActions } from "@/hooks/use-auth"
import { useWorkspaceManager } from "@/lib/hooks"
import { WorkflowBuilderProvider } from "@/providers/builder"
import { ScopeProvider } from "@/providers/scopes"
import { WorkflowProvider } from "@/providers/workflow"
import { WorkspaceIdProvider } from "@/providers/workspace-id"

const NO_ORG_MEMBERSHIPS_DETAIL = "User has no organization memberships"

function isNoOrgMembershipsError(error: unknown): boolean {
  if (!(error instanceof ApiError)) {
    return false
  }
  const body = error.body
  if (!body || typeof body !== "object") {
    return false
  }
  const detail =
    "detail" in body ? (body as { detail?: unknown }).detail : undefined
  return detail === NO_ORG_MEMBERSHIPS_DETAIL
}

export default function WorkspaceLayout({
  children,
}: {
  children: React.ReactNode
}) {
  const router = useRouter()
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
  const requestedWorkspaceExists = useMemo(() => {
    if (!workspaceId || !workspaces) {
      return false
    }
    return workspaces.some((workspace) => workspace.id === workspaceId)
  }, [workspaceId, workspaces])
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
    if (workspaceId && requestedWorkspaceExists) {
      setLastWorkspaceId(workspaceId)
    }
  }, [requestedWorkspaceExists, setLastWorkspaceId, workspaceId])

  const fallbackWorkspaceId = useMemo(() => {
    if (!workspaces || workspaces.length === 0) {
      return undefined
    }
    return lastViewedWorkspaceId ?? workspaces[0]?.id
  }, [lastViewedWorkspaceId, workspaces])

  useEffect(() => {
    if (
      workspacesLoading ||
      workspacesError ||
      !workspaceId ||
      requestedWorkspaceExists ||
      !fallbackWorkspaceId
    ) {
      return
    }
    router.replace(`/workspaces/${fallbackWorkspaceId}`)
  }, [
    fallbackWorkspaceId,
    requestedWorkspaceExists,
    router,
    workspaceId,
    workspacesError,
    workspacesLoading,
  ])

  if (workspacesLoading) {
    return <CenteredSpinner />
  }
  if (workspacesError && isNoOrgMembershipsError(workspacesError)) {
    return <NoOrganizationAccess />
  }
  if (workspacesError || !workspaces) {
    throw workspacesError
  }

  if (workspaceId && !requestedWorkspaceExists) {
    if (workspaces.length === 0) {
      return (
        <ScopeProvider>
          <NoWorkspaces />
        </ScopeProvider>
      )
    }
    return <CenteredSpinner />
  }

  let selectedWorkspaceId: string
  if (workspaceId) {
    selectedWorkspaceId = workspaceId
  } else if (lastViewedWorkspaceId) {
    selectedWorkspaceId = lastViewedWorkspaceId
  } else if (workspaces.length > 0) {
    selectedWorkspaceId = workspaces[0].id
  } else {
    return (
      <ScopeProvider>
        <NoWorkspaces />
      </ScopeProvider>
    )
  }

  return (
    <WorkspaceIdProvider workspaceId={selectedWorkspaceId}>
      <ScopeProvider>
        {workflowId ? (
          <WorkflowView
            workspaceId={selectedWorkspaceId}
            workflowId={workflowId}
          >
            <WorkspaceChildren>{children}</WorkspaceChildren>
          </WorkflowView>
        ) : (
          <WorkspaceChildren>{children}</WorkspaceChildren>
        )}
      </ScopeProvider>
    </WorkspaceIdProvider>
  )
}

function WorkspaceChildren({ children }: { children: React.ReactNode }) {
  const params = useParams<{
    workflowId?: string
    caseId?: string
  }>()
  const canReadWorkspace = useScopeCheck("workspace:read")
  const pathname = usePathname()
  const isWorkflowBuilder = !!params?.workflowId
  const isCaseDetail = !!params?.caseId
  const isInboxPage = pathname?.includes("/inbox")
  const isTableDetailPage = pathname?.match(/\/tables\/[^/]+/)
  const isSettingsPage = pathname?.includes("/settings")
  const isOrganizationPage = pathname?.includes("/organization")
  const isRegistryPage = pathname?.includes("/registry")

  if (canReadWorkspace === undefined) {
    return <CenteredSpinner />
  }

  if (canReadWorkspace === false) {
    return <WorkspaceAccessDenied />
  }

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

  // Inbox pages have their own layout with chat sidebar
  if (isInboxPage) {
    return <>{children}</>
  }

  // Table detail pages have their own layout with side panel
  if (isTableDetailPage) {
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

function WorkspaceAccessDenied() {
  const router = useRouter()

  return (
    <main className="container flex size-full max-w-[400px] flex-col items-center justify-center space-y-4">
      <Image src={TracecatIcon} alt="Tracecat" className="mb-4 size-16" />
      <h1 className="text-2xl font-semibold tracking-tight">Access denied</h1>
      <span className="text-center text-muted-foreground">
        You don&apos;t have permission to access this workspace.
      </span>
      <Button variant="outline" onClick={() => router.replace("/workspaces")}>
        Back to workspaces
      </Button>
    </main>
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
  const { user } = useAuth()
  const { logout } = useAuthActions()
  const canCreateWorkspace = useScopeCheck("workspace:create")
  const { createWorkspace } = useWorkspaceManager()
  const router = useRouter()
  const [isCreating, setIsCreating] = useState(false)

  const handleLogout = async () => {
    await logout()
  }

  const handleCreateWorkspace = async () => {
    setIsCreating(true)
    try {
      const workspace = await createWorkspace({ name: "New Workspace" })
      router.replace(`/workspaces/${workspace.id}/workflows`)
    } catch (error) {
      console.error("Error creating workspace", error)
      setIsCreating(false)
    }
  }

  return (
    <main className="container flex size-full max-w-[400px] flex-col items-center justify-center space-y-4">
      <Image src={TracecatIcon} alt="Tracecat" className="mb-4 size-16" />
      <h1 className="text-2xl font-semibold tracking-tight">No workspaces</h1>
      <span className="text-center text-muted-foreground">
        {canCreateWorkspace
          ? "There are no workspaces yet. Create one to get started."
          : "You are not a member of any workspace. Please contact your administrator."}
      </span>
      <div className="flex gap-2">
        {canCreateWorkspace && (
          <Button
            variant="default"
            onClick={handleCreateWorkspace}
            disabled={isCreating}
          >
            <Plus className="mr-2 size-4" />
            <span>{isCreating ? "Creating..." : "Create workspace"}</span>
          </Button>
        )}
        {user?.isSuperuser && (
          <Button variant="outline" asChild>
            <Link href="/admin">
              <Shield className="mr-2 size-4" />
              <span>Admin</span>
            </Link>
          </Button>
        )}
        <Button variant="outline" onClick={handleLogout}>
          <LogOut className="mr-2 size-4" />
          <span>Logout</span>
        </Button>
      </div>
    </main>
  )
}
