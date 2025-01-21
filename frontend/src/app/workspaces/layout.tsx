"use client"

import Image from "next/image"
import { useParams } from "next/navigation"
import { useAuth } from "@/providers/auth"
import { WorkflowBuilderProvider } from "@/providers/builder"
import { WorkflowProvider } from "@/providers/workflow"
import { WorkspaceProvider } from "@/providers/workspace"
import { LogOut } from "lucide-react"
import TracecatIcon from "public/icon.png"
import { ReactFlowProvider } from "reactflow"

import { useWorkspaceManager } from "@/lib/hooks"
import { Button } from "@/components/ui/button"
import { CenteredSpinner } from "@/components/loading/spinner"
import { DynamicNavbar } from "@/components/nav/dynamic-nav"

export default function WorkspaceLayout({
  children,
}: {
  children: React.ReactNode
}) {
  const { workspaces, workspacesLoading, workspacesError } =
    useWorkspaceManager()
  const { workspaceId } = useParams<{ workspaceId?: string }>()
  if (workspacesLoading) {
    return <CenteredSpinner />
  }
  if (workspacesError || !workspaces) {
    throw workspacesError
  }
  let wsId: string
  if (workspaceId) {
    wsId = workspaceId
  } else if (workspaces.length > 0) {
    wsId = workspaces[0].id
  } else {
    return <NoWorkspaces />
  }

  return (
    <WorkspaceProvider workspaceId={wsId}>
      <WorkflowProvider workspaceId={wsId}>
        <ReactFlowProvider>
          <WorkflowBuilderProvider>
            <div className="no-scrollbar flex h-screen max-h-screen flex-col overflow-hidden">
              {/* DynamicNavbar needs a WorkflowProvider and a WorkspaceProvider */}
              <DynamicNavbar />
              <div className="grow overflow-auto">{children}</div>
            </div>
          </WorkflowBuilderProvider>
        </ReactFlowProvider>
      </WorkflowProvider>
    </WorkspaceProvider>
  )
}

function NoWorkspaces() {
  const { logout } = useAuth()
  const handleLogout = async () => {
    await logout()
  }
  return (
    <main className="container flex size-full max-w-[400px] flex-col items-center justify-center space-y-4">
      <Image src={TracecatIcon} alt="Tracecat" className="mb-4 size-16" />
      <h1 className="text-2xl font-semibold tracking-tight">No Workspaces</h1>
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
