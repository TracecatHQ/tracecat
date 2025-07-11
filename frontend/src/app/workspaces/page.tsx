"use client"

import { useRouter } from "next/navigation"
import { useEffect } from "react"
import { CenteredSpinner } from "@/components/loading/spinner"
import { useWorkspaceManager } from "@/lib/hooks"

export default function WorkspacesPage() {
  const { workspaces, createWorkspace, getLastWorkspaceId } =
    useWorkspaceManager()
  const lastWorkspaceId = getLastWorkspaceId()
  const router = useRouter()

  useEffect(() => {
    // Redirect to the last viewed workspace
    if (lastWorkspaceId) {
      console.log("Redirecting to last workspace", lastWorkspaceId)
      router.replace(`/workspaces/${lastWorkspaceId}/workflows`)
      return
    }

    // Redirect to the first workspace
    if (workspaces) {
      console.log("Redirecting to first workspace")
      if (workspaces.length === 0) {
        // Create a default workspace on first login
        console.log("Creating a new workspace")
        createWorkspace({ name: "New Workspace" })
          .then((workspace) =>
            router.replace(`/workspaces/${workspace.id}/workflows`)
          )
          .catch((error) => {
            console.error("Error creating workspace", error)
          })
      } else {
        router.replace(`/workspaces/${workspaces[0].id}/workflows`)
      }
    }
  }, [lastWorkspaceId, workspaces, router, createWorkspace])

  // Return a loading indicator while waiting for redirection
  return <CenteredSpinner />
}
