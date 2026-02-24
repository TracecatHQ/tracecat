"use client"

import { useRouter } from "next/navigation"
import { useEffect, useRef } from "react"
import { CenteredSpinner } from "@/components/loading/spinner"
import { useWorkspaceManager } from "@/lib/hooks"

export default function WorkspacesPage() {
  const {
    workspaces,
    workspacesError,
    workspacesLoading,
    workspacesFetching,
    createWorkspace,
    getLastWorkspaceId,
    clearLastWorkspaceId,
  } = useWorkspaceManager()
  const router = useRouter()
  const hasStartedWorkspaceCreationRef = useRef(false)

  useEffect(() => {
    // Determine which workspace the user should land on
    if (
      workspacesLoading ||
      workspacesFetching ||
      workspacesError ||
      !workspaces
    ) {
      return
    }

    if (workspaces.length === 0) {
      if (hasStartedWorkspaceCreationRef.current) {
        return
      }
      hasStartedWorkspaceCreationRef.current = true
      console.log("Creating a new workspace")
      createWorkspace({ name: "New Workspace" })
        .then((workspace) => router.replace(`/workspaces/${workspace.id}`))
        .catch((error) => {
          console.error("Error creating workspace", error)
          hasStartedWorkspaceCreationRef.current = false
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
    } else if (lastViewedId && lastViewedId.trim().length > 0) {
      clearLastWorkspaceId()
    }

    if (!targetWorkspaceId) {
      targetWorkspaceId = workspaces[0]?.id
    }

    if (targetWorkspaceId) {
      router.replace(`/workspaces/${targetWorkspaceId}`)
    }
  }, [
    clearLastWorkspaceId,
    createWorkspace,
    getLastWorkspaceId,
    router,
    workspaces,
    workspacesError,
    workspacesFetching,
    workspacesLoading,
  ])

  // Return a loading indicator while waiting for redirection
  return <CenteredSpinner />
}
