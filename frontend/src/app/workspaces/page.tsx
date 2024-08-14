"use client"

import { useRouter } from "next/navigation"

import { useWorkspaceManager } from "@/lib/hooks"

export default function WorkspacesPage() {
  const { workspaces, createWorkspace, getLastWorkspaceId } =
    useWorkspaceManager()
  const lastWorkspaceId = getLastWorkspaceId()
  const router = useRouter()

  // Redirect to the last viewed workspace
  if (lastWorkspaceId) {
    // This only works if you're the same user.
    console.log("Redirecting to last workspace", lastWorkspaceId)
    return router.replace(`/workspaces/${lastWorkspaceId}/workflows`)
  }
  // Redirect to the first workspace
  if (workspaces) {
    console.log("Redirecting to first workspace", workspaces[0].id)
    if (workspaces.length === 0) {
      console.log("Creating a new workspace")
      createWorkspace({ requestBody: { name: "New Workspace" } })
        .then((workspace) =>
          router.replace(`/workspaces/${workspace.id}/workflows`)
        )
        .catch((error) => {
          console.error("Error creating workspace", error)
          throw new Error("Could not create workspace")
        })
    } else {
      return router.replace(`/workspaces/${workspaces[0].id}/workflows`)
    }
  } else {
    // Some error occurred
    throw new Error("Could not load workspaces")
  }
}
