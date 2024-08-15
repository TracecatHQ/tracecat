"use client"

import { useParams } from "next/navigation"
import { WorkflowProvider } from "@/providers/workflow"
import { WorkspaceProvider } from "@/providers/workspace"

import { useWorkspaceManager } from "@/lib/hooks"
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
  if (!workspaceId) {
    // If no workspaceId is provided, use the first workspace
    if (workspaces.length === 0) {
      throw new Error("No workspaces found")
    } else {
      wsId = workspaces[0].id
    }
  } else {
    wsId = workspaceId
  }

  console.log("Redirecting to workspace", wsId)
  return (
    <WorkspaceProvider workspaceId={wsId}>
      <WorkflowProvider workspaceId={wsId}>
        <div className="no-scrollbar flex h-screen max-h-screen flex-col">
          {/* DynamicNavbar needs a WorkflowProvider */}
          <DynamicNavbar />
          {children}
        </div>
      </WorkflowProvider>
    </WorkspaceProvider>
  )
}
