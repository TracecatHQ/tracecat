"use client"

import { useRouter } from "next/navigation"
import { useEffect } from "react"
import { CenteredSpinner } from "@/components/loading/spinner"
import { useWorkspaceManager } from "@/lib/hooks"

export default function WorkspacesPage() {
  const { workspaces, createWorkspace, getLastWorkspaceId } =
    useWorkspaceManager()
  const router = useRouter()

  useEffect(() => {
    // Determine which workspace the user should land on
    if (!workspaces) {
      return
    }

    if (workspaces.length === 0) {
      console.log("Creating a new workspace")
      createWorkspace({ name: "New Workspace" })
        .then((workspace) =>
          router.replace(`/workspaces/${workspace.id}/workflows`)
        )
        .catch((error) => {
          console.error("Error creating workspace", error)
        })
      return
    }

    let targetWorkspaceId: string | undefined

    const lastViewedId = getLastWorkspaceId()
    if (
      lastViewedId &&
      lastViewedId.trim().length > 0 &&
      workspaces.some((workspace) => workspace.id === lastViewedId)
    ) {
      targetWorkspaceId = lastViewedId
    }

    if (!targetWorkspaceId) {
      targetWorkspaceId = workspaces[0]?.id
    }

    if (targetWorkspaceId) {
      router.replace(`/workspaces/${targetWorkspaceId}/workflows`)
    }
  }, [createWorkspace, getLastWorkspaceId, router, workspaces])

  // Return a loading indicator while waiting for redirection
  return <CenteredSpinner />
}
