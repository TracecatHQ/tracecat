"use client"

import { useRouter } from "next/navigation"
import { useEffect } from "react"
import { CenteredSpinner } from "@/components/loading/spinner"
import { useWorkspaceManager } from "@/lib/hooks"
import { getWorkspaceLandingPath } from "@/lib/workspace-navigation"

export default function WorkspacesPage() {
  const { workspaces, getLastWorkspaceId } = useWorkspaceManager()
  const router = useRouter()

  useEffect(() => {
    // Determine which workspace the user should land on
    if (!workspaces) {
      return
    }

    if (workspaces.length === 0) {
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
      router.replace(getWorkspaceLandingPath(targetWorkspaceId))
    }
  }, [getLastWorkspaceId, router, workspaces])

  // Return a loading indicator while waiting for redirection
  return <CenteredSpinner />
}
