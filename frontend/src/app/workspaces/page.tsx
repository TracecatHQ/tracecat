"use client"

import { redirect } from "next/navigation"
import Cookies from "js-cookie"

import { useWorkspaceManager } from "@/lib/hooks"

export default function WorkspacesPage() {
  const { workspaces, createWorkspace } = useWorkspaceManager()
  const lastWorkspaceId = Cookies.get("__tracecat:workspaces:last-viewed")

  // Redirect to the last viewed workspace
  if (lastWorkspaceId) {
    return redirect(`/workspaces/${lastWorkspaceId}/workflows`)
  }
  // Redirect to the first workspace
  if (workspaces) {
    if (workspaces.length === 0) {
      createWorkspace({ requestBody: { name: "New Workspace" } })
    }
    return redirect(`/workspaces/${workspaces[0].id}/workflows`)
  }

  // Some error occurred
  throw new Error("Could not load workspaces")
}
