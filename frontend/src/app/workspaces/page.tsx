"use client"

import { useRouter } from "next/navigation"
import { useEffect, useMemo } from "react"
import { CenteredSpinner } from "@/components/loading/spinner"
import { useWorkspaceManager } from "@/lib/hooks"
import { useAuth } from "@/hooks/use-auth"
import { getDefaultWorkspacePreference } from "@/lib/user-settings"

export default function WorkspacesPage() {
  const { workspaces, createWorkspace, getLastWorkspaceId } =
    useWorkspaceManager()
  const { user } = useAuth()
  const router = useRouter()
  const defaultWorkspacePreference = useMemo(
    () => getDefaultWorkspacePreference(user?.settings),
    [user?.settings]
  )

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

    if (defaultWorkspacePreference.strategy === "specific") {
      const preferredId = defaultWorkspacePreference.workspaceId
      if (
        preferredId &&
        workspaces.some((workspace) => workspace.id === preferredId)
      ) {
        targetWorkspaceId = preferredId
      }
    }

    const lastViewedId = getLastWorkspaceId()
    if (
      !targetWorkspaceId &&
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
  }, [
    createWorkspace,
    defaultWorkspacePreference,
    getLastWorkspaceId,
    router,
    workspaces,
  ])

  // Return a loading indicator while waiting for redirection
  return <CenteredSpinner />
}
